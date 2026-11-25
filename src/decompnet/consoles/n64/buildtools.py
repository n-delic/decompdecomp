"""Use an already-installed matching IDO compiler when a public project's local compiler build is absent."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from ... import toolchains

HOST_BUILD_TOOLS = {"gcc", "cc", "as", "ld", "cpp", "c++", "g++", "clang", "ar", "objcopy", "strip", "make"}


def _is_gcc_272(driver: Path) -> bool:
    """Is this program GCC 2.7.2 (the KMC build many N64 projects commit as tools/gcc)?"""
    try:
        return b"2.7.2" in driver.read_bytes()
    except OSError:
        return False


def _in_installed_toolchains(path: Path) -> bool:
    """Is this file one of the installed toolchains' own (a project's tools/ folder that is a link to one)? Those are
    shared with every project and the app: a stand-in is never written over them."""
    try:
        return toolchains.root().resolve() in path.resolve().parents
    except OSError:
        return False


def _is_sn64_folder(folder: Path) -> bool:
    """The SN Systems GCC 2.7.2 a project keeps next to the KMC one (also "GNU C 2.7.2", so the KMC stand-in must
    not take it): its `as` is modern-asn64, which no KMC folder has."""
    return (folder / "modern-asn64.py").is_file() or "sn64" in folder.name.lower()


def _wrap_sn64_gcc(folders) -> None:
    """The project's SN `gcc` is a 32-bit Linux driver: a Mac runs the installed, natively built SN compiler
    (`sn-n64-272-970404`) through decompdecomp's driver (sn64_gcc.py) instead; the original stays as gcc.linux."""
    if sys.platform != "darwin" or toolchains.installed("sn-n64-272-970404") is None:
        return
    installed = toolchains.require("sn-n64-272-970404")
    driver = Path(__file__).with_name("sn64_gcc.py")
    for folder in folders:
        gcc, assembler = folder / "gcc", folder / "as"
        if _in_installed_toolchains(gcc):
            continue
        try:
            with open(gcc, "rb") as f:
                if f.read(4) != b"\x7fELF" or not assembler.is_file():
                    continue
        except OSError:
            continue
        gcc.rename(gcc.with_name("gcc.linux"))
        gcc.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{driver}" "{installed}" "{assembler.resolve()}" "$@"\n',
                       encoding="utf-8")
        gcc.chmod(0o755)


def _crc_tool(root: Path) -> None:
    """Public N64 projects fix the ROM's checksum with tools/n64crc, which their `make dependencies` compiles next to
    a whole IDO build. Compile just that one file when it's missing, so the final link step has its tool (a
    failure here stays silent: the project's own error then says what it lacks)."""
    source, tool = root / "tools" / "n64crc.c", root / "tools" / "n64crc"
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if source.is_file() and not tool.exists() and cc:
        try:
            subprocess.run([cc, str(source), "-o", str(tool)], capture_output=True, timeout=120,
                           stdin=subprocess.DEVNULL, check=False)
        except (OSError, subprocess.SubprocessError):
            pass


def _ido_hint(project) -> str:
    """Which IDO a project that doesn't name it in a path wants: what its README or Makefile says, else 5.3 (most
    N64 games)."""
    said = ""
    for name in ("README.md", "Makefile"):
        try:
            said += (project.root / name).read_text(errors="replace", encoding="utf-8")
        except OSError:
            pass
    mention = re.search(r"(?i)ido[ _-]*(?:version\s*)?v?(5\.3|7\.1)", said)
    return mention[1] if mention else "5.3"


def _provide_sn64(project) -> None:
    """A project whose own setup downloads the SN64 compiler from the sn64 release into tools/bin (cc1n64.exe,
    cc1pln64.exe) finds the installed, pinned copies of those exact files there instead; setup runs nothing of its own
    that downloads. Only files that are installed are linked, and an existing tools/bin file is left alone."""
    texts = ""
    for name in ("configure.py", "setup", "Makefile"):
        try:
            texts += (project.root / name).read_text(errors="replace", encoding="utf-8") + "\n"
        except OSError:
            pass
    if not re.search(r"tools/bin/cc1(?:pl)?n64\.exe", texts):
        return
    folder = project.root / "tools" / "bin"
    for tid in ("sn-n64-281", "sn-n64-cxx"):
        if toolchains.installed(tid) is None:
            continue
        for item in sorted(toolchains.require(tid).glob("*.exe")):
            link = folder / item.name
            if not link.exists() and not link.is_symlink():
                folder.mkdir(parents=True, exist_ok=True)
                link.symlink_to(item)


def _provide_compiler_dirs(project) -> None:
    """A project that expects a compiler folder it doesn't ship for this computer (tools/gcc_2.7.2/macos,
    tools/ido/7.1) gets a link to the installed one, in its own tree: the folder is never in git."""
    if os.name == "nt":
        return
    texts = ""
    for name in ("Makefile", "Makefile.common", "README.md", "setup.py", "setup.sh", "install_compilers.sh"):
        try:
            texts += (project.root / name).read_text(errors="replace", encoding="utf-8") + "\n"
        except OSError:
            pass
    wanted: list[tuple[str, str]] = []
    if re.search(r"tools/gcc[_-]2\.7\.2/(?:macos|\$\(DETECTED_OS\))", texts) and sys.platform == "darwin":
        wanted.append(("tools/gcc_2.7.2/macos", "gcc-2.7.2-kmc"))
    if sys.platform == "darwin":
        # tools/gcc_kmc/$(DETECTED_OS)/2.7.2, tools/gcc_egcs/$(DETECTED_OS)/1.1.2-4: the project's `make setup` downloads
        # a tarball for each (one of them may no longer exist); the installed compilers stand in. Each makefile
        # (the root's, a library's) names its own OS folder (mac, macos).
        for name in ("Makefile", "lib/Makefile"):
            try:
                own = (project.root / name).read_text(errors="replace", encoding="utf-8")
            except OSError:
                continue
            mac = re.search(r"DETECTED_OS\s*:?=\s*(mac\w*)", own)
            for kind, version in set(re.findall(r"tools/(gcc_kmc|gcc_egcs)/\$\(DETECTED_OS\)/(2\.7\.2|1\.1\.2-4)", own)):
                if mac and (kind, version) in (("gcc_kmc", "2.7.2"), ("gcc_egcs", "1.1.2-4")):
                    wanted.append((f"tools/{kind}/{mac[1]}/{version}",
                                   "gcc-2.7.2-kmc" if kind == "gcc_kmc" else "gcc-egcs-2.91.66"))
        # the folder itself, as a Linux-only project names it (tools/gcc-2.7.2/gcc; $(TOOLS_DIR)/gcc-2.7.2/)
        for name in set(re.findall(r"(?:\btools|\$\(TOOLS(?:_DIR)?\))/(gcc[_-]2\.7\.2)/(?!macos|\$\(DETECTED_OS\))",
                                   texts)):
            wanted.append((f"tools/{name}", "gcc-2.7.2-kmc"))
    # tools/build/cc/<compiler>: where a project's `install_compilers.sh` unpacks the compilers it would download
    # (Paper Mario's: its own GCC 2.8.1, KMC's 2.7.2, EGCS, IDO 5.3), as empty folders the installed ones fill
    for name, tid in (("gcc2.7.2", "gcc-2.7.2-kmc"), ("gcc", "gcc-2.8.1-pm"), ("egcs", "gcc-egcs-2.91.66"),
                      ("ido5.3", "ido-5.3")):
        if re.search(rf"tools/build/cc/{re.escape(name)}(?![\w.])", texts):
            wanted.append((f"tools/build/cc/{name}", tid))
    for version in set(re.findall(r"tools/ido/(5\.3|7\.1)(?![\d])", texts)):
        wanted.append((f"tools/ido/{version}", f"ido-{version}"))
    for folder, version in set(re.findall(r"(tools/ido(5\.3|7\.1)[\w.-]*)(?=\s|$|/)", texts, re.M)):
        wanted.append((folder, f"ido-{version}"))
    if sys.platform == "darwin":
        for folder, version in set(re.findall(r"(tools/ido[\w-]*)/(?:macos|\$\(DETECTED_OS\))/(5\.3|7\.1)(?![\d])", texts)):
            wanted.append((f"{folder}/macos/{version}", f"ido-{version}"))
    # tools/<name> the Makefile runs that isn't there (not built, or not for this computer) but the computer has
    path = os.pathsep.join([os.environ.get("PATH", ""), str(Path.home() / ".cargo" / "bin")])
    for name in sorted(set(re.findall(r"(?<![\w/.-])tools/([A-Za-z][\w-]*)(?=\s|$|\))", texts, re.M))):
        mine = project.root / "tools" / name
        host = shutil.which(name, path=path)
        if host and not mine.exists() and not mine.is_symlink() and (project.root / "tools").is_dir():
            mine.write_text(f'#!/bin/sh\nexec "{host}" "$@"\n', encoding="utf-8")
            mine.chmod(0o755)
    _provide_sn64(project)
    _provide_built_ido(project, texts)
    for rel, tid in wanted:
        target = project.root / rel
        if toolchains.installed(tid) is None:
            continue
        source = toolchains.require(tid)
        if target.is_dir() and not target.is_symlink() and not (target / "cc").exists() and source.is_dir():
            # the project's own folder for it holds sources it would build the compiler from: the built one goes in
            for item in source.iterdir():
                if not (target / item.name).exists():
                    (target / item.name).symlink_to(item)
            continue
        if target.exists() or target.is_symlink():
            continue
        if source.is_dir() and tid == "gcc-2.7.2-kmc" and "mabi32" in texts and sys.platform == "darwin":
            # the project's compiler is told `-mabi32`, which the installed assembler doesn't know (it assembles
            # o32 already): a folder of its own, with links to every installed program and an `as` that drops it
            target.mkdir(parents=True, exist_ok=True)
            for item in source.iterdir():
                if item.name != "as" and not (target / item.name).exists():
                    (target / item.name).symlink_to(item)
            mine = source / "as"
            if mine.is_file():
                (target / "as").write_text('#!/bin/sh\nfor a; do shift; [ "$a" = -mabi32 ] || set -- "$@" "$a"; done\n'
                                           f'exec "{mine}" "$@"\n', encoding="utf-8")
                (target / "as").chmod(0o755)
        elif source.is_dir():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(source, target_is_directory=True)


def _provide_built_ido(project, texts: str) -> None:
    """A project that builds its own IDO from the IRIX binaries (`build/recomp/5.3/cc: build/recomp/recomp`, Perfect
    Dark's) is given the installed one, as a *copy* in that folder: the recompiled one crashes here ("mmap: Invalid
    argument"), and a folder link would let the Makefile's own rules write their output through it into the installed
    compiler. The copies are newer than what the rules depend on (stand-ins for the tools that would be built), so
    make considers each program up to date and runs nothing."""
    import time

    found = re.findall(r"^((?:[\w.-]+/)*build/(?:[\w.-]+/)*(5\.3|7\.1)/cc)[ \t]*:([^=\n]*)(?:\n|$)", texts, re.M)
    now = time.time()
    for rel, version, prerequisites in found:
        tid = f"ido-{version}"
        if toolchains.installed(tid) is None:
            continue
        source = toolchains.require(tid)
        folder = project.root / rel.rsplit("/", 1)[0]
        if not (source / "cc").is_file():
            continue
        if folder.is_symlink() and _in_installed_toolchains(folder):
            folder.unlink()  # (an earlier link: a rule would have written the installed compiler)
        if (folder / "cc").exists() and not folder.is_symlink():
            continue
        if folder.exists() and not folder.is_dir():
            folder.unlink()
        folder.mkdir(parents=True, exist_ok=True)
        for wanted in prerequisites.split():
            stub = project.root / wanted
            if "%" in wanted or "$" in wanted or stub.exists() or not wanted.startswith("build/"):
                continue
            stub.parent.mkdir(parents=True, exist_ok=True)
            stub.write_text("", encoding="utf-8")
            stub.chmod(0o755)
            os.utime(stub, (now, now))
        for item in source.iterdir():
            if item.is_file() and item.suffix != ".c" and not (folder / item.name).exists():
                shutil.copy2(item, folder / item.name)
                os.utime(folder / item.name, (now + 5, now + 5))


def _wrap_foreign_ido(project) -> None:
    """On a Mac a project's own copy of IDO is usually a Linux program (its CI's) its build can't run ("Exec format
    error"). Replace each such `cc` driver, once, with a script that runs the installed IDO of the version its
    path names (5.3 or 7.1); the original is kept beside it as cc.linux. Only drivers that are Linux ELF files
    are touched, so a project's own Mac build, or its own makefile's redirect, stays as it was."""
    if sys.platform != "darwin":
        return
    done = project.root / ".decomp-wtf" / "build-tools" / ".ido-wrapped"
    tools = project.root / "tools"
    if not tools.is_dir():
        return
    # (the project's own tools/, and those of a library it carries: lib/ultralib/tools/ido/cc)
    roots = [tools, *sorted(project.root.glob("lib/*/tools")), *sorted(project.root.glob("libs/*/tools"))]
    seen = "\n".join(str(r) for r in roots)  # (a submodule setup fetches later adds its tools: look again then)
    # (looked at every time: a project's own setup step can fetch its Linux compiler again over a wrapper)
    drivers = [(root, driver) for root in roots if root.is_dir() for driver in root.glob("**/cc")]
    for tools_root, driver in drivers:
        if _in_installed_toolchains(driver):
            continue
        if len(driver.relative_to(tools_root).parts) > 5 or driver.is_symlink() or not driver.is_file():
            continue
        if driver.parent.name == "bin" and driver.parent.parent.name == "usr":
            continue  # (usr/bin/cc of an IRIX tree: the input ido-static-recomp turns into this computer's compiler)
        try:
            with open(driver, "rb") as f:
                magic = f.read(4)
        except OSError:
            continue
        macho = (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe")
        if magic in macho:
            # a project's own Mac IDO whose files lost their execute bit (a zip, or Windows, on the way to git)
            for program in driver.parent.iterdir():
                if program.is_file() and not program.is_symlink() and not program.stat().st_mode & 0o111:
                    with open(program, "rb") as f:
                        if f.read(4) in macho:
                            program.chmod(0o755)
            try:  # a fat binary whose slices are damaged ("malformed Mach-o file") is no more use than a Linux one
                runs = subprocess.run([str(driver)], capture_output=True, timeout=20, stdin=subprocess.DEVNULL
                                      ).returncode != 127
            except (OSError, subprocess.SubprocessError):
                runs = False
            if runs:
                continue
        if magic != b"\x7fELF" and magic not in macho:
            continue
        version = re.search(r"(?<!\d)(5)[._](3)(?!\d)|(?<!\d)(7)[._](1)(?!\d)", str(driver.relative_to(tools_root)))
        if version:
            tid = "ido-" + ("5.3" if version[1] else "7.1")
        else:  # tools/ido/cc: the README or Makefile says which
            tid = "ido-" + _ido_hint(project)
        if toolchains.installed(tid) is None:
            continue  # compiler installation and license acceptance stay in Setup
        source = toolchains.require(tid) / "cc"
        if not source.is_file():
            continue
        driver.rename(driver.with_name("cc.linux"))
        driver.write_text(f'#!/bin/sh\nexec "{source}" "$@"\n', encoding="utf-8")
        driver.chmod(0o755)
    # A project's own copy of the KMC-style GCC 2.7.2 (tools/gcc_2.7.2/{gcc,cc1,as,...}, Linux programs): the
    # installed one stands in for each program it has under that name.
    # (or tools/gcc itself, beside its cc1: a project that keeps the compiler's programs directly in tools/)
    # (the project's own tools/, and those of a library it carries, lib/ultralib/tools/gcc)
    def found(*patterns):
        return [d for root in roots if root.is_dir() for pattern in patterns for d in root.glob(pattern)]

    sn = {d.parent for d in found("*/gcc", "*/*/gcc") if _is_sn64_folder(d.parent)}
    _wrap_sn64_gcc(sn)
    plain = {d.parent for d in found("gcc", "*/gcc") if (d.parent / "cc1").is_file()
             and d.is_file() and not d.is_symlink() and _is_gcc_272(d) and d.parent not in sn}
    for folder in ({d.parent for d in found("*gcc*2.7.2*/gcc", "*/*gcc*2.7.2*/gcc")} | plain) - sn:
        if toolchains.installed("gcc-2.7.2-kmc") is None:
            break
        installed = toolchains.require("gcc-2.7.2-kmc")
        for program in folder.iterdir():
            mine = installed / program.name
            if (not program.is_file() or program.is_symlink() or not mine.is_file() or program.suffix
                    or _in_installed_toolchains(program)):
                continue
            with open(program, "rb") as f:
                if f.read(4) != b"\x7fELF":
                    continue
            program.rename(program.with_name(program.name + ".linux"))
            extra = f' -B"{installed}/"' if program.name == "gcc" else ""
            if program.name == "as":
                # (the project's assembler takes the o32 spelling `-mabi32`; the installed one is o32 already and
                # doesn't know it)
                program.write_text(f'#!/bin/sh\nfor a; do shift; [ "$a" = -mabi32 ] || set -- "$@" "$a"; done\n'
                                   f'exec "{mine}" "$@"\n', encoding="utf-8")
            else:
                program.write_text(f'#!/bin/sh\nexec "{mine}"{extra} "$@"\n', encoding="utf-8")
            program.chmod(0o755)
    _update_old_mac_assembler(project, tools)
    # The same for a project's other committed Linux programs (tools/pigment64, ...) when the host has the tool:
    # `cargo install` and the system's own packages are where those come from.
    path = os.pathsep.join(["/opt/homebrew/bin", "/usr/local/bin", os.environ.get("PATH", ""),
                            str(Path.home() / ".cargo" / "bin")])
    # (a program in a folder named for it and its version, tools/gzip-1.2.4/gzip, counts too: GNU gzip's output is
    # what a compressed-overlay build expects, and Homebrew's is first on the path for that)
    versioned = [t for t in tools.glob("*/*") if re.fullmatch(re.escape(t.parent.name.split("-")[0]) + r"[-_.\d]*",
                                                              t.parent.name) and t.name == t.parent.name.split("-")[0]]
    for tool in [*tools.glob("*"), *versioned]:
        if not tool.is_file() or tool.is_symlink() or not tool.stat().st_mode & 0o111 or tool.suffix:
            continue
        try:
            with open(tool, "rb") as f:
                if f.read(4) != b"\x7fELF":
                    continue
        except OSError:
            continue
        host = shutil.which(tool.name, path=path)
        # (never a compiler, assembler or linker: the host's of that name builds for the host, not the game)
        if host and Path(host).resolve() != tool.resolve() and tool.name not in HOST_BUILD_TOOLS:
            tool.rename(tool.with_name(tool.name + ".linux"))
            tool.write_text(f'#!/bin/sh\nexec "{host}" "$@"\n', encoding="utf-8")
            tool.chmod(0o755)
    done.parent.mkdir(parents=True, exist_ok=True)
    done.write_text(seen, encoding="utf-8")


def _update_old_mac_assembler(project, tools: Path) -> None:
    """A project's own Mac build of the KMC GCC 2.7.2 (tools/gcc_2.7.2/macos) can carry an older `as` than its
    Makefile expects: the compiler is told `-Wa,--vr4300mul-off` and the assembler answers "unrecognized option".
    The installed KMC `as` knows the option, so it stands in for the old one (the old one is kept as as.old)."""
    if sys.platform != "darwin" or toolchains.installed("gcc-2.7.2-kmc") is None:
        return
    makefile = ""
    for name in ("Makefile", "Makefile.common"):
        try:
            makefile += (project.root / name).read_text(errors="replace", encoding="utf-8") + "\n"
        except OSError:
            pass
    # (`-Wa,--vr4300mul-off`, or `-mabi32` on the compiler's own command line, which it hands to the assembler)
    markers = [m for m in ("vr4300mul-off", "mabi32") if m in makefile]
    if not markers:
        return
    mine = toolchains.require("gcc-2.7.2-kmc") / "as"
    if not mine.is_file():
        return
    for old in {*tools.glob("*gcc*2.7.2*/macos/as"), *tools.glob("*gcc*2.7.2*/as")}:
        if old.is_symlink() or not old.is_file() or _in_installed_toolchains(old):
            continue
        try:
            data = old.read_bytes()
        except OSError:
            continue
        if data[:4] not in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe") or any(m.encode() in data for m in markers):
            continue
        old.rename(old.with_name("as.old"))
        old.write_text(f'#!/bin/sh\nfor a; do shift; [ "$a" = -mabi32 ] || set -- "$@" "$a"; done\n'
                       f'exec "{mine}" "$@"\n', encoding="utf-8")
        old.chmod(0o755)


def command(project, argv: list[str]) -> list[str]:
    argv = _redirect(project, argv)
    makefile = project.root / "Makefile"
    if (sys.platform == "darwin" and Path(argv[0]).name in ("make", "gmake") and makefile.is_file()
            and not any(a.startswith("RUN_CC_CHECK=") for a in argv[1:])
            and re.search(r"^RUN_CC_CHECK\s*[:?]?=", makefile.read_text(encoding="utf-8", errors="replace"), re.M)):
        argv = [*argv, "RUN_CC_CHECK=0"]
    if (sys.platform == "darwin" and Path(argv[0]).name in ("make", "gmake") and makefile.is_file()
            and not any(a.startswith("CC_HOST=") for a in argv[1:])
            and re.search(r"^CC_HOST\s*[:?]?=\s*(?:gcc|cc|clang)\s*$",
                          makefile.read_text(encoding="utf-8", errors="replace"), re.M)):
        argv = [*argv, "CC_HOST=true"]  # the same host pass under another name  # the project's own switch for its host-gcc pass (clang answers gcc here)
    if (sys.platform == "darwin" and Path(argv[0]).name in ("make", "gmake") and makefile.is_file()
            and not any(a.startswith("CC_CHECK=") for a in argv[1:])
            and re.search(r"^[ \t]*CC_CHECK\s*[:+?]*=\s*(?:(?:gcc|cc|clang)\b(?:[^\n]*-fsyntax-only|[ \t]*$)|\$\(\w*CC\w*\)[^\n]*-fsyntax-only)",
                          makefile.read_text(encoding="utf-8", errors="replace"), re.M)):
        # a host-gcc syntax pass before each compile: clang answers `gcc` here and rejects its -m32 headers; the
        # real compile (IDO) is what builds the game
        argv = [*argv, "CC_CHECK=true"]
    if (sys.platform == "darwin" and Path(argv[0]).name in ("make", "gmake") and makefile.is_file()
            and not any(a.startswith("CPP=") for a in argv[1:])):
        text = makefile.read_text(encoding="utf-8", errors="replace")
        # a macOS branch that points CPP at `clang -E -P -x c` while a recipe pipes into `$(CPP) -E -P ...` (stdin):
        # clang wants a `-` for that and says "no input files"; the cpp of the build path (GNU's) reads stdin as the
        # Linux branch's `cpp` does
        if re.search(r"^[ \t]*CPP\s*:?=\s*clang\b[^\n]*-E", text, re.M) and re.search(r"\|\s*\$\(CPP\)", text):
            argv = [*argv, "CPP=cpp"]
    return argv


def _redirect(project, argv: list[str]) -> list[str]:
    if os.name == "nt":
        return argv  # these compiler binaries run inside WSL on Windows
    _wrap_foreign_ido(project)
    _provide_compiler_dirs(project)
    if Path(argv[0]).name not in ("make", "gmake") or any(a.startswith(("IDO=", "CC=")) for a in argv[1:]):
        return argv
    makefile = project.root / "Makefile"
    if not makefile.is_file():
        return argv
    _crc_tool(project.root)
    text = makefile.read_text(encoding="utf-8", errors="replace")
    # The variable is usually IDO; some projects name it CC, indented inside an if/else (a community decomp of a
    # racing game builds its compiler in tools/ and picks it with `CC := ...` under `else`). A command-line
    # value beats the makefile's own assignment, so one override reaches both spellings.
    declared = re.search(r"^[ \t]*(IDO|CC)[ \t]*[:?]?=[ \t]*([^\s#]*(?:/(?:5\.3|7\.1)/|ido)(?:[^\s#]*/)?cc)[ \t]*(?:#.*)?$",
                         text, re.M)
    qemu = None if declared else re.search(
        r"^[ \t]*(IDO|CC)[ \t]*[:?]?=[ \t]*\S*qemu-irix[^\n#]*(?<!\s)[ \t]*(?:#.*)?$", text, re.M)
    if qemu:
        # `IDO := ~/qemu-irix -L tools/ido_irix tools/ido_irix/usr/bin/cc`: an IRIX IDO run through the author's
        # own qemu, which a Mac doesn't have; the installed IDO (a native build of the same compiler) replaces it
        tid = "ido-" + _ido_hint(project)
        if toolchains.installed(tid) is None:
            return argv
        source = toolchains.require(tid)
        tools = project.root / ".decomp-wtf" / "build-tools"
        tools.mkdir(parents=True, exist_ok=True)
        (tools / ".gitignore").write_text("*\n", encoding="utf-8")
        alias = tools / tid
        if not (source / "cc").is_file():
            return argv
        if not alias.exists():
            alias.symlink_to(source, target_is_directory=True)
        return [*argv, qemu[1] + "=" + (alias / "cc").relative_to(project.root).as_posix()]
    if not declared:
        return argv
    name, path = declared[1], declared[2]
    # Resolve only unique literal declarations (usually TOOLS := tools), never Make functions or conditions.
    for variable in re.findall(r"\$\((\w+)\)", path):
        values = set(re.findall(rf"^{re.escape(variable)}\s*[:?]?=\s*([^\s#$]+)\s*$", text, re.M))
        if len(values) == 1:
            path = path.replace(f"$({variable})", values.pop())
    version = re.search(r"/(5\.3|7\.1)/", path)
    if "$" in path or not path.endswith("/cc") or (project.root / path).exists():
        return argv
    tid = "ido-" + (version[1] if version else _ido_hint(project))
    if toolchains.installed(tid) is None:
        return argv  # compiler installation and license acceptance stay in Setup
    source = toolchains.require(tid)
    if not (source / "cc").is_file():
        return argv
    # A relative directory link keeps sibling compiler programs available and avoids spaces in the build command.
    tools = project.root / ".decomp-wtf" / "build-tools"
    tools.mkdir(parents=True, exist_ok=True)
    (tools / ".gitignore").write_text("*\n", encoding="utf-8")
    alias = tools / tid
    if not alias.exists():
        alias.symlink_to(source, target_is_directory=True)
    if not alias.is_symlink() or alias.resolve() != source.resolve():
        return argv
    if name == "CC" and not Path(path).is_absolute():  # (/opt/ido/cc is the author's: never written to)
        # (a command-line CC would reach the project's sub-makes too, which build their host tools with it: the
        # compiler's own folder is linked where the Makefile looks instead)
        folder = (project.root / path).parent
        if not folder.exists() and not folder.is_symlink():
            folder.parent.mkdir(parents=True, exist_ok=True)
            folder.symlink_to(source, target_is_directory=True)
            return argv
    return [*argv, name + "=" + (alias / "cc").relative_to(project.root).as_posix()]
