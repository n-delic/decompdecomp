"""A decomp project on disk: what kind it is, what it needs, how to set it up, build a unit and diff it.

Layouts set up automatically:
- manifest:  the project ships `.decomp.wtf.json`: {"game": {"what", "path", "sha1"}, "setup": [[cmd...], ...]}.
- dtk:       GameCube/Wii (and similar) projects from dtk-template: configure.py + config/<version>/config.yml,
             built with ninja; the disc image goes in orig/<version>/.
- configure: other configure.py + ninja projects (many PS2 and DS decomps).
- splat:     N64/PlayStation-style projects (splat yaml + Makefile); the game file goes where the yaml's
             target_path says and is checked against its sha1.
Everything else (CMake, justfiles, custom scripts) is set up by the user following its README; once the build
has produced objdiff.json, decompdecomp takes over. Nearly every project on decomp.dev ends up with one, since
that's how its progress is reported.
"""

from __future__ import annotations

import collections
import datetime
import functools
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from decompnet.hostenv import NO_WINDOW, console_python, stop_process_tree

from .. import consoles, toolchains
from ..limits import CompilerMemoryWatchdog, attach_job, capped, default_cap, killed_for_memory, release_job, stop_group
from ..network import NetworkError
from . import objdiff

STATE = ".decomp-wtf"
# What a project covers (its manifest's `scope`): the game's own code, with the code its set-aside list names (library
# code, middleware, an SDK, the C runtime) kept from agents and out of progress; or the whole program, every function
# open and counted. A manifest without one covers the game's own code, as every project did before there was a choice.
# The scope decides only that: an import analyses the same code whatever it is. Code an older import left out
# altogether (no functions, no source files) a console can bring in later without a new import (Console.unanalysed);
# such a manifest may still say what as `set_aside_unimported`, which nothing reads any more.
GAME, PROGRAM = "game", "program"
SCOPES = (GAME, PROGRAM)
_CONFIG_CACHE: dict[str, tuple] = {}  # objdiff.json path -> (stat key, parsed, units by name)
_CONFIG_LOCK = threading.Lock()
_DOCKER_SETUP_LOCK = threading.Lock()
# Agents may only edit source and header files, never the project's build setup, targets or verification.
EDITABLE = re.compile(r"\.(c|cc|cpp|cxx|h|hh|hpp|hxx|inc|inl)$", re.I)
FORBIDDEN_PARTS = {".git", STATE, "orig", "build", "asm", "expected", "tools", "config", ".github", "assets", "bin"}


def _sha1(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _file_sha1(path: Path) -> str:
    """A file's SHA-1, hashed again only when its size or modification time changes (ROM Explorer asks often)."""
    stat = path.stat()
    return _stat_sha1(str(path), stat.st_size, stat.st_mtime_ns)


@functools.lru_cache(maxsize=64)
def _stat_sha1(path: str, size: int, mtime_ns: int) -> str:
    return _sha1(Path(path))


class ForkError(NetworkError):
    """Shown to the user or agent as is."""


@dataclass
class Unit:
    name: str
    target: Path | None
    base: Path | None
    source: str | None  # path relative to the project root
    complete: bool
    scratch: dict | None
    options: dict | None = None  # the unit's own objdiff options
    mappings: dict | None = None  # objdiff symbol mappings (target name -> base name)
    auto: bool = False  # split off by dtk because no file covers it (auto_00_80003100_text)
    scaffold: bool = False  # app-created candidate, not an upstream source file
    scaffold_dir: str | None = None  # nested project whose build owns the candidate


@functools.lru_cache(maxsize=64)
def _git_env(root: Path) -> dict | None:
    """Commits need an identity: when git has none set up on this computer, commits are by decompdecomp."""
    res = subprocess.run(["git", "config", "user.email"], cwd=root, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    if res.returncode == 0 and res.stdout.strip():
        return None
    who = {"GIT_AUTHOR_NAME": "decompdecomp", "GIT_AUTHOR_EMAIL": "noreply@decompdecomp.invalid"}
    return {**os.environ, **who, "GIT_COMMITTER_NAME": who["GIT_AUTHOR_NAME"], "GIT_COMMITTER_EMAIL": who["GIT_AUTHOR_EMAIL"]}


# dtk-template's configure.py: Object(NonMatching, "path.c"), also over several lines, MatchingFor("GALE01")...
_OBJECT = re.compile(r'\bObject\(\s*(NonMatching|Matching|Equivalent|MatchingFor\([^()]*\))\s*,\s*"([^"]+)"', re.S)
_SRC_DIR = re.compile(r'^\s*config\.src_dir\s*=\s*Path\(\s*"([^"]+)"\s*\)', re.M)
_CHECK = re.compile(r"^build (\S+[/\\]ok): check\b")  # dtk-template writes build\<version>\ok on Windows
# build.ninja's top-level variables (`PY = /path/python`) and rules' commands (`  command = /path/tool ...`): the
# program each runs first (build_problem)
_NINJA_PROGRAM = re.compile(r"^(?:([A-Za-z_][\w.-]*)|\s+command)\s*=\s*(.*)$")
# A target the build couldn't make: ninja's "FAILED: out.o", make's "make: *** [Makefile:12: out.o] Error 1"
_FAILED = re.compile(r"^FAILED: (.+)$|^g?make(?:\[\d+\])?: \*\*\* \[(?:[^\]]*?:\d+: )?([^\]]+)\] Error")
_BUILD_ERROR = re.compile(r"(?:fatal error:|\berror:|\bError:|No such file or directory|"
                          r"command not found|cannot execute binary|ModuleNotFoundError:|Traceback \(most recent|^within )")
# CodeWarrior's linker warnings are harmless and can run to thousands of lines.
_NOISE = re.compile(r"^#{1,3}\s*(mwldeppc\.exe Linker Warning:|FORCEACTIVE symbol|(doesn't )?exist\.\s+Ignored\.|"
                    r"Section '\.note\.split' is unknown)")


_NO_WORK = "ninja: no work to do."  # what ninja says when everything asked for is built
UNIT_SECONDS = 600  # a unit's build, at most (a run of several builds them side by side: the same)
OUTPUT_SECONDS = 30  # after a step ends, how long its output may go on


def _descendants(pid: int) -> list[int]:
    """Every process started under `pid`, by `ps` (Linux and macOS alike)."""
    try:
        listed = subprocess.run(["ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    children: dict[int, list[int]] = {}
    for line in listed.splitlines():
        p = line.split()
        if len(p) == 2 and p[0].isdigit() and p[1].isdigit():
            children.setdefault(int(p[1]), []).append(int(p[0]))
    found, todo = [], [pid]
    while todo:
        for c in children.get(todo.pop(), []):
            if c not in found:
                found.append(c)
                todo.append(c)
    return found


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _stop_tree(proc: subprocess.Popen) -> None:
    """Stop a build step and every process it started: on Windows the tree; elsewhere every process under it, each
    asked first, then killed. Not just its process group: ninja puts each command in a group of its own, so a
    compiler that ignored being asked outlived the step. The tree is read before anything stops, since a command
    whose ninja is gone has another parent and can't be found from the step any more."""
    if os.name == "nt":
        stop_process_tree(proc)
        return
    tree = _descendants(proc.pid)

    def signal_all(sig: int) -> None:
        for stop in (lambda: os.killpg(proc.pid, sig), *(lambda p=p: os.kill(p, sig) for p in tree)):
            try:
                stop()
            except OSError:
                pass

    signal_all(signal.SIGTERM)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and (proc.poll() is None or any(_alive(p) for p in tree)):
        time.sleep(0.1)
    signal_all(signal.SIGKILL)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def _tool_dirs() -> list[Path]:
    """Where decompdecomp's own build tools (the ninja Python package's ninja) are: beside its Python, or, in the
    Windows setup's bundle (packages installed with --target), in the `bin` folder beside those packages, which the
    bundle's launcher didn't put on PATH."""
    bundled = Path(__file__).resolve().parents[2] / "bin"
    return [Path(sys.executable).parent, *([bundled] if bundled.is_dir() else [])]


def _real_default_goal(makefile: str) -> list[str]:
    """`make` with no target runs the Makefile's first rule, which in some projects is `clean` (written before
    `all`) or `help`: those name the target that builds instead. Nothing when the default already builds."""
    if re.search(r"^\.DEFAULT_GOAL\s*[:?]?=", makefile, re.M):
        return []
    first = next((m[1] for m in re.finditer(r"^([A-Za-z][\w-]*)\s*:(?!=)", makefile, re.M)), None)
    if first not in ("clean", "distclean", "help"):
        return []
    return next(([t] for t in ("all", "build", "rom") if re.search(rf"^{t}\s*:", makefile, re.M)), [])


def _makefile_version_word(text: str, var: str, version: str) -> str:
    """The word a Makefile's `ifeq ($(VERSION), US)` blocks compare its version variable with, for a game whose
    version is named differently (a ROM called baserom.u.z64, sha1 file ge007.u.sha1): the same word in another case,
    or the block that sets `COUNTRYCODE := u` and builds the plain `OUTCODE := $(COUNTRYCODE)` image. The
    version itself when the Makefile compares nothing or already knows it."""
    words = re.findall(rf"^\s*ifeq\s*\(\$\({re.escape(var)}\),\s*([\w.-]+)\)", text, re.M)
    if not words or version in words:
        return version
    for word in words:
        if word.lower() == version.lower():
            return word
    for block in re.finditer(rf"^\s*ifeq\s*\(\$\({re.escape(var)}\),\s*([\w.-]+)\)(.*?)^\s*endif", text, re.M | re.S):
        body = block.group(2)
        code = re.search(r"^\s*COUNTRYCODE\s*:?=\s*(\S+)", body, re.M)
        out = re.search(r"^\s*OUTCODE\s*:?=\s*(\S+)", body, re.M)
        if code and out and code.group(1) == version and out.group(1) in ("$(COUNTRYCODE)", version):
            return block.group(1)
    return version


def _version_var(makefile: str) -> str | None:
    """The variable a Makefile picks the game's version by, when it has a default for it (`VERSION ?= us`, or Perfect
    Dark's `ROMID ?= ntsc-final`): `make <var>=<version>` builds another."""
    return next((v for v in ("VERSION", "ROMID") if re.search(rf"^{v}\s*[:?]?=", makefile, re.M)), None)


def _setup_targets(makefile: str) -> list[str]:
    """The Makefile's own preparation targets, in the order its README runs them. `assets` counts only when its recipe
    runs Torch or ZAPD (a Makefile whose README says `make -C tools`, `make assets`, `make`): the headers its compile step
    includes are made there, and a build without them stops at the first #include. Another project's `assets`
    target is its own business."""
    found = [t for t in ("tools", "toolchain", "setup", "extract") if re.search(rf"^{t}\s*:", makefile, re.M)]
    if "decompress" not in found and re.search(r"^decompress\s*:", makefile, re.M) and re.search(r"(?i)uncompressed", makefile):
        # (a game whose retail image is compressed: the uncompressed copy `extract` reads is made from it first)
        found.insert(found.index("extract") if "extract" in found else len(found), "decompress")
    if re.search(r"^prerequisites\s*:[^\n]*(?:tools|extract)", makefile, re.M):
        found.append("prerequisites")  # (one that builds the host tools and extracts assets: rules like the RSP's use them unordered)
    recipe = re.search(r"^assets\s*:[^\n]*\n((?:(?:[ \t]|ifn?eq|else|endif|#)[^\n]*\n?)*)", makefile, re.M)
    if recipe and re.search(r"(?i)torch|\bextract_assets\.py|\bzapd\b", recipe[1]):  # (the Zelda decomps: ZAPD's headers)
        found.append("assets")
    return found


def _mac_cxx_env(root: Path) -> dict[str, str]:
    """A project that builds a host tool with CMake and fetches spdlog (Torch, which several N64 projects extract their
    assets with) fails on the Mac's clang: spdlog's bundled fmt wants a consteval check that clang rejects, and the
    project sets CMAKE_CXX_FLAGS itself, so neither CXXFLAGS nor a command-line flag reaches it. CMake does read flags
    that ride in the CXX variable: fmt's own switch for turning that check off goes there. Only a CMake project that
    names spdlog gets it; any other build is left as it was."""
    found: dict[str, str] = {}
    for cmake in (root / "tools").glob("*/CMakeLists.txt"):
        # (CMake 4 refuses the fetched libraries' old minimum versions unless told the policy floor, by environment too)
        found.setdefault("CMAKE_POLICY_VERSION_MINIMUM", "3.5")
        # (an Apple Silicon clang fuses a*b+c into one rounding by default; the x86 builds the projects' bytes come
        # from don't, and an asset tool that writes floats then differs in their last bit)
        found.setdefault("CXX", "c++ -ffp-contract=off")
        try:
            if "spdlog" in cmake.read_text(encoding="utf-8", errors="replace"):
                found["CXX"] = "c++ -ffp-contract=off -DFMT_CONSTEVAL= -Wno-error=parentheses"
        except OSError:
            continue
    return found


def _mac_host_headers(root: Path) -> Path | None:
    """The folder of stand-in headers for a project's own host tools (tools/*/*.c) that include a Linux-only header the
    Mac doesn't have: `<crypt.h>` (glibc's; Perfect Dark's mkrom includes it from its gzip code and calls nothing in it).
    An empty header is the stand-in, written once under the project's own state folder; None when nothing needs one."""
    names = ("crypt.h",)
    wanted: set[str] = set()
    for source in list((root / "tools").glob("*/*.c"))[:400]:
        try:
            text = source.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        wanted.update(n for n in names if re.search(rf'^\s*#\s*include\s*[<"]{re.escape(n)}[>"]', text, re.M)
                      and not (root / "tools" / source.parent.name / n).exists())
    if not wanted:
        return None
    folder = root / ".decomp-wtf" / "host-include"
    folder.mkdir(parents=True, exist_ok=True)
    for name in wanted:
        if not (folder / name).exists() and not (Path("/usr/include") / name).exists():
            (folder / name).write_text("/* a stand-in: the Mac has no such header and the project's tool uses nothing in it */\n",
                                       encoding="utf-8")
    return folder


def _iconv_override(root: Path, tool_dirs: list[Path], path: str) -> str | None:
    """A Makefile that names Homebrew's libiconv for its text preprocessing (`ICONV := $(shell brew --prefix)/opt/libiconv/
    bin/iconv`, as the Zelda decomps' do) fails on a Mac without that formula: "iconv: No such file". The iconv decompdecomp
    puts on the build's PATH (the shim that keeps a backslash after a multi-byte character as ASCII, as GNU's does) or the
    system's does the job, so make is told to use it. Nothing changes when the formula is installed."""
    try:
        text = (root / "Makefile").read_text(errors="replace", encoding="utf-8")
    except OSError:
        return None
    if not re.search(r"^\s*ICONV\s*:?=.*libiconv", text, re.M):
        return None
    if any((Path(p) / "opt/libiconv/bin/iconv").is_file() for p in ("/opt/homebrew", "/usr/local")):
        return None
    for folder in tool_dirs:
        if (folder / "iconv").is_file():
            return str(folder / "iconv")
    return shutil.which("iconv", path=path)


def _tool(name: str) -> str | None:
    """A build tool from PATH or from decompdecomp's own environment (the ninja Python package)."""
    found = shutil.which(name)
    if found:
        return found
    exe = f"{name}.exe" if os.name == "nt" else name
    return next((str(d / exe) for d in _tool_dirs() if (d / exe).exists()), None)


# #97: a public dtk project hashes the files it was built from, and some of those (a main.dol made from an ELF,
# a .rel module) aren't on the retail disc. dtk stops at the hash; say what it means and what the user can do.
_HASH_MISMATCH = re.compile(r"Hash mismatch: expected ([0-9a-fA-F]+)(?:\.{3}|…)?, but was ([0-9a-fA-F]+)", re.I)
_DTK_OBJECT = re.compile(r"While (?:loading|processing) object '([^']+)'")


def _hash_mismatch(tail: str) -> str | None:
    """What dtk's file hash mismatch means for the user, or None when this failure isn't one."""
    m = _HASH_MISMATCH.search(tail)
    if m is None:
        return None
    objects = _DTK_OBJECT.findall(tail[:m.start()])  # the file just before the hash: the one dtk was loading
    if not objects:
        return None
    obj, (expected, got) = objects[-1], m.groups()
    return (f"your copy's {obj} isn't the one this project was built against (the project expects hash "
            f"{expected[:8]}..., yours is {got[:8]}...). Your dump is likely fine: some projects are set up from "
            "files that aren't on the retail disc (check the project's README for which file and where it comes "
            "from). Or start the game from your own copy instead: choose New project → Open a game file.")


# How many YAML files are read looking for the config that splits the game file.
MAX_SPLAT_CANDIDATES = 400
MADE_FROM_MAX = 2000  # targets followed up from a build's failures (failed_units): a few levels in practice
QUERY_BATCH = 50  # targets per `ninja -t query`
# never_defined: macros compilers define themselves without a leading underscore (IDO's, on SGI and N64 code; the
# pinned KMC GCC 2.7.2's MACH, EE-GCC 2.96's R5900, read off their `-dM -E`; Windows projects' own WIN32), where the
# project's files may never mention them
PREDEFINED = {"LANGUAGE_C", "LANGUAGE_ASSEMBLY", "MIPSEB", "MIPSEL", "R3000", "R4000", "R5900", "MACH", "WIN32",
              "WIN64"}
SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".cp", ".h", ".hh", ".hpp", ".hxx", ".inc", ".inl"}
BUILD_SUFFIXES = {".py", ".ninja", ".mk", ".json", ".toml", ".yml", ".yaml", ".cmake", ".sh", ".bat", ".ps1", ".lcf", ".ld"}
BUILD_FILES = {"Makefile", "makefile", "GNUmakefile", "CMakeLists.txt"}
NOT_SEARCHED = {".git", "build", "orig", "extract", "node_modules", STATE}
GENERATED_DIRS = {"build"}  # of NOT_SEARCHED, where the build writes headers of its own (never_defined reads those)
HEADER_SUFFIXES = {".h", ".hh", ".hpp", ".hxx", ".inc", ".inl"}
SEARCH_MAX = 16 << 20  # bigger files aren't sources or build files


def project_macro(name: str) -> bool:
    """Whether a macro may be the project's own rather than the compiler's: those start with "_", have lowercase
    letters (`mips`, `unix`) or are known ones (PREDEFINED)."""
    return not name.startswith("_") and name == name.upper() and name not in PREDEFINED


def _ninja_where(args: list[str]) -> list[str]:
    """Of a project's ninja arguments (objdiff.json `custom_args`), the ones that say which build it is: `-f FILE`
    and `-C DIR`, apart or joined (`-fother.ninja`). Targets and the rest (`-j8`, `all_source`) aren't a query's."""
    out, i = [], 0
    while i < len(args):
        a = str(args[i])
        if a in ("-f", "-C") and i + 1 < len(args):
            out += [a, str(args[i + 1])]
            i += 1
        elif a[:2] in ("-f", "-C") and len(a) > 2:
            out.append(a)
        i += 1
    return out


def _query_outputs(text: str) -> set[str]:
    """The targets under "outputs:" in `ninja -t query`'s answer: what each queried target is an input of."""
    found, section = set(), ""
    for line in text.splitlines():
        if not line.startswith(" "):
            section = ""  # the next queried target
        elif not line.startswith("    "):
            section = line.strip().split(":")[0]  # "input", "outputs", "validations"
        elif section == "outputs":
            found.add(line.strip())
    return found


class ForkProject:
    def __init__(self, root: Path, *, create_state: bool = True):
        self.root = Path(root)
        if create_state:
            (self.root / STATE).mkdir(parents=True, exist_ok=True)

    def _game_consoles(self):
        """The named console, or each console's file preparation when a public project's platform is unknown."""
        from .. import consoles

        return [consoles.get(self.platform)] if self.platform else consoles.startable()

    # ------------------------------------------------------------------ what is it

    @property
    def manifest(self) -> dict | None:
        p = self.root / ".decomp.wtf.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def update_manifest(self, **keys) -> dict:
        """Change keys of `.decomp.wtf.json` (a None removes one), keeping the rest; returns the new manifest."""
        manifest = self.manifest or {}
        for k, v in keys.items():
            if v is None:
                manifest.pop(k, None)
            else:
                manifest[k] = v
        (self.root / ".decomp.wtf.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return manifest

    @property
    def scope(self) -> str:
        """What the project covers (SCOPES): PROGRAM only when its manifest says so."""
        return PROGRAM if (self.manifest or {}).get("scope") == PROGRAM else GAME

    def kind(self) -> str:
        """What kind of build the project has: decompdecomp's own manifest, dtk-template, a configure.py, splat
        with a Makefile, an objdiff.json alone, or something else."""
        if self.manifest:
            return "manifest"
        if (self.root / "configure.py").exists() and list(self.root.glob("config/*/config.yml")):
            return "dtk"
        if (self.root / "configure.py").exists():
            return "configure"
        if self._splat_yaml() and (self.root / "Makefile").exists():
            return "splat"
        if (self.root / "objdiff.json").exists():
            return "objdiff"
        return "custom"

    def readme(self, limit: int = 70) -> str:
        """The README's build/setup section (plain text, from the repository: untrusted, display only)."""
        for name in ("README.md", "README.MD", "readme.md", "README.rst", "README.txt", "README"):
            p = self.root / name
            if p.exists():
                lines = p.read_text(errors="replace", encoding="utf-8").splitlines()
                break
        else:
            return ""
        def heading(i: int) -> bool:  # markdown "# Title" or an underlined (setext/rst) title, not a badge row
            if "![" in lines[i] or "[!" in lines[i]:
                return False
            if lines[i].lstrip().startswith("#"):
                return True
            return bool(lines[i].strip()) and i + 1 < len(lines) and bool(re.fullmatch(r"\s*[=-]{3,}\s*", lines[i + 1]))

        wanted = re.compile(r"build|setup|set up|getting started|usage|install|requirements|prerequisite", re.I)
        start = next((i for i in range(len(lines)) if heading(i) and wanted.search(lines[i])), 0)
        return "\n".join(lines[start:start + limit]).strip()

    def versions(self) -> list[str]:
        return sorted(p.parent.name for p in self.root.glob("config/*/config.yml"))

    def detect_platform(self) -> str:
        """Read the console declared by a public project's manifest or Splat config without running its code."""
        if value := (self.manifest or {}).get("platform"):
            return value
        # Public objdiff projects declare their console on per-unit scratch
        # configurations. Accept a single consistent connector identity only.
        try:
            objdiff = json.loads((self.root / 'objdiff.json').read_text(encoding='utf-8'))
            units = objdiff.get('units', [])
            declared = {consoles.get(unit['scratch']['platform']).id for unit in units
                        if isinstance(unit, dict) and isinstance(unit.get('scratch'), dict)
                        and isinstance(unit['scratch'].get('platform'), str)}
            known = {c.id for c in consoles.CONSOLES}
            if len(declared) == 1 and declared <= known:return next(iter(declared))
            inferred = {console.id for console in consoles.CONSOLES if console.recognizes_objdiff(objdiff)}
            if len(inferred) == 1:return next(iter(inferred))
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        path = self._splat_yaml()
        if path is None:
            return ""
        import yaml

        try:
            config = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, yaml.YAMLError):
            return ""
        options = config.get("options") if isinstance(config, dict) else None
        value = options.get("platform") if isinstance(options, dict) else None
        if not isinstance(value, str):
            return ""
        console = consoles.get(value.strip().lower())
        return console.id if console.id in {c.id for c in consoles.CONSOLES} else ""

    def _splat_yaml(self) -> Path | None:
        for p in sorted(self.root.glob("*.yaml")) + sorted(self.root.glob("config/*.yaml")) + sorted(self.root.glob("config/**/*.yaml")) \
                + sorted(self.root.glob("versions/*/*.yaml")) + sorted(self.root.glob("ver/*/splat.yaml")):
            try:
                text = p.read_text(errors="replace", encoding="utf-8")
            except OSError:
                continue
            if "segments:" in text and "options:" in text:
                return p
        return None

    def _splat_yaml_of_placed_rom(self) -> Path | None:
        """Of several splat configs (one per edition: config/jp, config/us), the one whose original ROM is the file
        in the project: its target_path exists and has the config's SHA-1. None when no edition says so."""
        import yaml

        from .splatrefs import config_sha1

        for path in [*sorted(self.root.glob("*.yaml")), *sorted(self.root.glob("config/*.yaml")),
                     *sorted(self.root.glob("config/**/*.yaml")), *sorted(self.root.glob("versions/*/*.yaml")),
                     *sorted(self.root.glob("ver/*/splat.yaml"))]:
            try:
                config = yaml.safe_load(path.read_text(encoding="utf-8"))
                options = config["options"]
                digest = config_sha1(path, config)
                target = self._inside(str(path.parent / options.get("base_path", ".") / options["target_path"]))
            except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError):
                continue
            if target and isinstance(digest, str) and (self.root / target).is_file() and \
                    _file_sha1(self.root / target) == digest.lower():
                return path
        return None

    def _splat_version_configs(self) -> dict[str, Path]:
        """Only infer editions from the YAML template the project's Makefile actually uses."""
        makefile = self.root / "Makefile"
        if not makefile.is_file():
            return {}
        make = makefile.read_text(errors="replace", encoding="utf-8")
        basename = re.search(r"^BASENAME\s*[:?]?=\s*([A-Za-z0-9_-]+)\s*$", make, re.M)
        suffix = "$(BASENAME).$(VERSION).yaml"
        if not basename:
            # (a Makefile that writes the name out: `splat split blastcorps.$(VERSION).yaml`)
            basename = re.search(r"(?<![\w$./-])([A-Za-z0-9_-]+)\.\$\(VERSION\)\.yaml", make)
            if not basename:
                return self._splat_variable_configs(make)
            suffix = basename[0]
        nested = "config/$(VERSION)/" + suffix
        if nested in make:
            paths = self.root.glob("config/*/" + basename[1] + ".*.yaml")
        elif suffix in make:
            paths = self.root.glob(basename[1] + ".*.yaml")
        else:
            return {}
        configs = {}
        for path in sorted(paths):
            version = path.name[len(basename[1]) + 1:-5]
            if (re.fullmatch(r"[A-Za-z0-9._-]+", version) and
                    (nested not in make or path.parent.name == version) and
                    self.root.resolve() in path.resolve().parents):
                configs[version] = path
        return configs

    def _splat_variable_configs(self, make: str) -> dict[str, Path]:
        """Editions whose splat config the Makefile names through variables: `VERSION_DIR := versions/$(VERSION)` and
        `SPLAT_YAML := $(VERSION_DIR)/$(TARGET).yaml`. Only a path the `splat` command of the Makefile really reads
        counts (the variable is expanded textually; VERSION stays the placeholder)."""
        variables = {m[1]: m[2].strip() for m in re.finditer(r"^(\w+)\s*[:?]?=\s*(.*?)\s*$", make, re.M)}

        def expand(text: str, depth: int = 0) -> str:
            if depth > 8:
                return text
            return re.sub(r"\$\((\w+)\)", lambda m: (m[0] if m[1] == "VERSION" or m[1] not in variables
                                                      else expand(variables[m[1]], depth + 1)), text)

        reads = {expand(value) for name, value in variables.items() if re.search(r"splat", name, re.I)
                 and name != "SPLAT_DIR"} | {expand(m[1]) for m in re.finditer(r"splat\s+split\s+(\S+)", make)}
        found = {}
        for text in reads:
            for candidate in re.findall(r"[\w./$()-]*\$\(VERSION\)[\w./$()-]*\.ya?ml", text):
                if "$(" in candidate.replace("$(VERSION)", ""):
                    continue
                if candidate.count("$(VERSION)") != 1:
                    continue
                head, tail = candidate.split("$(VERSION)")
                for path in sorted(self.root.glob(candidate.replace("$(VERSION)", "*"))):
                    relative = path.relative_to(self.root).as_posix()
                    version = relative[len(head):len(relative) - len(tail)]
                    if (re.fullmatch(r"[A-Za-z0-9._-]+", version) and self.root.resolve() in path.resolve().parents):
                        found[version] = path
        return found

    def _makefile_rom(self, version: str | None) -> dict | None:
        """The ROM a Makefile reads, when nothing else names it: `baserom.$(VERSION).z64` (VERSION is the Makefile's
        default, or the version the user's ROM matched) or a literal `baserom.z64`. No SHA-1: the project's own
        check (its .sha1/.md5 file) says whether it is the right game."""
        text = ""
        for name in ("Makefile", "Makefile.common", "configure"):  # (`configure`: a shell script that checks the ROM)
            try:
                text += (self.root / name).read_text(errors="replace", encoding="utf-8") + "\n"
            except OSError:
                pass
        default = re.search(r"^VERSION\s*[:?]?=\s*([A-Za-z0-9._-]+)\s*$", text, re.M)
        ver = version or (default.group(1) if default else None)
        found = re.search(r"((?:[\w-]+/|\$\(\w+\)/)*)baserom(\.\$\((?:VERSION|REGION)\))?\.z64", text) or re.search(
            r"((?:[\w-]+/)*)baserom\.([a-z]{1,4})\.z64", text)
        if found and found.re.groups == 2 and found.group(2) and found.group(2) != "z64" and "(" not in found.group(2) \
                and found.re.pattern.endswith(r"\.z64") and "VERSION" not in found.re.pattern:
            rel = self._inside(f"{found.group(1)}baserom.{found.group(2)}.z64") or f"{found.group(1)}baserom.{found.group(2)}.z64"
            return {"what": f"your original game ROM ({rel})", "path": rel, "sha1": None, "auto": True,
                    "help": "Supply an original cartridge ROM in .z64, .v64 or .n64 byte order; decompdecomp saves a "
                            ".z64 copy where the project's Makefile reads it, and the project's own check says "
                            "whether it is the right release."}
        if not found:
            return self._readme_rom()
        rel = f"{found.group(1)}baserom.{ver}.z64" if found.group(2) else f"{found.group(1)}baserom.z64"
        for _ in range(4):  # $(BASEROM_DIR)/ and $(VERSION): the Makefile's own plain assignments, or this game's
            left = re.search(r"\$\((\w+)\)", rel)
            if not left:
                break
            name = left.group(1)
            value = ver if name in ("VERSION", "REGION") else None
            if value is None:
                assigned = re.findall(rf"^{name}\s*[:?]?=\s*([^\s#]+)\s*$", text, re.M)
                value = assigned[0] if len(set(assigned)) == 1 else None
            if value is None or (name in ("VERSION", "REGION") and not ver):
                return self._readme_rom()
            rel = rel.replace(left.group(0), value)
        rel = self._inside(rel) or rel
        return {"what": f"your original game ROM ({rel})", "path": rel, "sha1": None, "auto": True,
                "help": "Supply an original cartridge ROM in .z64, .v64 or .n64 byte order; decompdecomp saves a "
                        ".z64 copy where the project's Makefile reads it, and the project's own check says "
                        "whether it is the right release."}

    def _readme_rom(self) -> dict | None:
        """Where the README says to put the ROM ("copy it to roms/baserom.us.z64"), for a project whose build files
        don't spell it out: the one literal path it names, or the North American one when it names several."""
        try:
            text = (self.root / "README.md").read_text(errors="replace", encoding="utf-8")
        except OSError:
            return None
        names = list(dict.fromkeys(re.findall(r"(?<![\w/.-])((?:\.?/)?(?:[\w-]+/)*baserom(?:\.[\w]+)*\.z64)\b", text)))
        names = [n[2:] if n.startswith("./") else n for n in names if "$" not in n and ".." not in n]
        pick = names[0] if len(names) == 1 else next(
            (n for n in names if re.search(r"(?:^|[./-])(us|u|usa|ntsc-u)(?:[./-]|$)", n)), None)
        rel = self._inside(pick) if pick else None
        if not rel:
            return None
        return {"what": f"your original game ROM ({rel})", "path": rel, "sha1": None, "auto": True,
                "help": "Supply an original cartridge ROM in .z64, .v64 or .n64 byte order; decompdecomp saves a "
                        ".z64 copy where the project's README says to put it."}

    def decomp_yaml_roms(self) -> dict[str, tuple[str, str | None]]:
        """{version: (where the project wants its ROM, its SHA-1 or None)} from the project's decomp.yaml (the
        decomp.dev convention many community decomps keep: a `versions` list whose `paths` name the original image
        as `target` or `baserom`). The hash is the entry's `sha1`, else a `checksum*.sha1` / `<version>.sha1` file
        that names it (its build's own name; the hash is the same one the original has when the game matches).
        Only cartridge-ROM paths count: a disc or archive is another console's setup."""
        path = self.root / "decomp.yaml"
        if not path.is_file():
            return self._splat_dir_roms() or self._sha1_file_roms()
        import yaml

        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, yaml.YAMLError):
            return {}
        entries = data.get("versions") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            return self._base_rom_of(data)
        out: dict[str, tuple[str, str | None]] = {}
        for entry in entries:
            paths = entry.get("paths") if isinstance(entry, dict) else None
            name = str(entry.get("name") or "") if isinstance(entry, dict) else ""
            if not isinstance(paths, dict) or not name:
                continue
            target = paths.get("target") or paths.get("baserom")
            if isinstance(target, str) and re.search(r"decomp|uncompress", Path(target).name, re.I) and isinstance(
                    paths.get("compressed_target"), str):
                target, entry = paths["compressed_target"], {**entry, "sha1": None}  # (the cartridge's, whose hash is not this one)
            rel = self._inside(target) if isinstance(target, str) else None
            if not rel or not rel.lower().endswith((".z64", ".n64", ".v64")):
                continue
            digest = entry.get("sha1")
            if not (isinstance(digest, str) and re.fullmatch(r"[0-9a-fA-F]{40}", digest)):
                digest = None
                files = sorted(self.root.glob(f"*{name}*.sha1")) or (sorted(self.root.glob("checksum*.sha1"))
                                                                    if len(entries) == 1 else [])
                if not files and re.fullmatch(r"[\w.-]+", name):  # (ver/<name>/checksum.sha1: Paper Mario's)
                    files = sorted(self.root.glob(f"ver/{name}/checksum*.sha1")) + sorted(
                        self.root.glob(f"{name}/checksum*.sha1"))
                for f in files[:3]:
                    found = re.search(r"\b[0-9a-fA-F]{40}\b", f.read_text(errors="replace", encoding="utf-8"))
                    if found:
                        digest = found.group(0)
                        break
            out[name] = (rel, digest.lower() if digest else None)
        return out

    def _splat_dir_roms(self) -> dict[str, tuple[str, str | None]]:
        """{version: (ROM path, SHA-1)} for a project that keeps one splat config per edition in ver/splat
        (`dkr.us.v77.yaml`, each with its `sha1` and a `target_path` inside the project): the version is the part of
        the file name after the basename, which the project's Makefile reads as REGION.VERSION."""
        import yaml

        out: dict[str, tuple[str, str | None]] = {}
        for path in sorted((self.root / "ver" / "splat").glob("*.*.*.yaml")):
            try:
                config = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, yaml.YAMLError):
                continue
            options = config.get("options") if isinstance(config, dict) else None
            target, base = (options or {}).get("target_path"), (options or {}).get("base_path", ".")
            digest = config.get("sha1") if isinstance(config, dict) else None
            if not (isinstance(target, str) and isinstance(base, str) and isinstance(digest, str)
                    and re.fullmatch(r"[0-9a-fA-F]{40}", digest) and target.lower().endswith((".z64", ".n64", ".v64"))):
                continue
            rel = self._inside(str(path.parent / base / target))
            if rel:
                out[path.name.split(".", 1)[1].removesuffix(".yaml")] = (rel, digest.lower())
        return out

    def _base_rom_of(self, data) -> dict[str, tuple[str, str | None]]:
        """decomp.yaml's own `base_rom: baserom.z64` (the decomp.dev agent configuration) for a project whose splat
        config names the ROM by the author's absolute path: the cartridge goes where decomp.yaml says, its hash from
        the splat config."""
        target = data.get("base_rom") if isinstance(data, dict) else None
        rel = self._inside(target) if isinstance(target, str) else None
        if not rel or not rel.lower().endswith((".z64", ".n64", ".v64")):
            return {}
        import yaml

        from .splatrefs import config_sha1

        config_path = self._splat_yaml()
        try:
            config = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path else None
        except (OSError, ValueError, yaml.YAMLError):
            return {}
        digest = config_sha1(config_path, config) if isinstance(config, dict) else None
        if not (isinstance(digest, str) and re.fullmatch(r"[0-9a-fA-F]{40}", digest)):
            return {}
        make = (self.root / "Makefile").read_text(errors="replace", encoding="utf-8") if (
            self.root / "Makefile").is_file() else ""
        default = re.search(r"^VERSION\s*[:?]?=\s*([A-Za-z0-9._-]+)\s*$", make, re.M)
        return {default.group(1) if default else "default": (rel, digest.lower())}

    def rom_originals(self) -> dict[str, str]:
        """{version: SHA-1} of the original ROMs the project can be set up with (splat's, else decomp.yaml's)."""
        if originals := self.splat_baseroms():
            return originals
        return {v: digest for v, (_, digest) in self.decomp_yaml_roms().items() if digest}

    def md5_roms(self) -> dict[str, tuple[str, set[str]]]:
        """{version: (where the project wants its ROM, the MD5s of the originals it accepts)} for a project that names
        its originals by MD5, not SHA-1: `baseroms/<version>/checksum*compressed.md5` (the Zelda decomps: the ROM goes
        in `baseroms/<version>/baserom.z64`), or a table of `| ROM ID | description | MD5 |` rows in the README with the
        ROM named `<game>.<ROM ID>.z64` there (Perfect Dark). The hash the build is checked against is the original's
        when the game matches, as with a decomp.yaml's."""
        out: dict[str, tuple[str, set[str]]] = {}
        base = self.root / "baseroms"
        if base.is_dir():
            for folder in sorted(p for p in base.iterdir() if p.is_dir()):
                for f in sorted(folder.glob("checksum*compressed.md5")):
                    try:
                        found = re.findall(r"\b[0-9a-fA-F]{32}\b", f.read_text(errors="replace", encoding="utf-8"))
                    except OSError:
                        continue
                    rel = self._inside(f"baseroms/{folder.name}/baserom.z64") or f"baseroms/{folder.name}/baserom.z64"
                    out.setdefault(folder.name, (rel, set()))[1].update(m.lower() for m in found)
        if out:
            return out
        try:
            text = (self.root / "README.md").read_text(errors="replace", encoding="utf-8")
        except OSError:
            return {}
        rows = {m[0]: m[1].lower() for m in re.findall(
            r"^\|\s*`?([A-Za-z0-9][A-Za-z0-9._-]*)`?\s*\|[^|\n]*\|\s*`?([0-9a-fA-F]{32})`?\s*\|", text, re.M)}
        for stem, ident in re.findall(r"(?<![\w/.-])([A-Za-z0-9_-]+)\.([A-Za-z0-9][A-Za-z0-9._-]*)\.z64\b", text):
            if ident in rows:
                template = f"{stem}.{{}}.z64"
                return {v: (self._inside(template.format(v)) or template.format(v), {md5})
                        for v, md5 in rows.items()}
        return {}

    def _sha1_file_roms(self) -> dict[str, tuple[str, str]]:
        """{version: (where its ROM goes, SHA-1)} for a project with one `<name>.<version>.sha1` file per version (the
        hash of the ROM its build makes, the original's when the game matches) and a README, Makefile or script that
        says the original goes in `baserom.<version>.z64` (the Mario 64 decomp)."""
        files = {m[2]: f for f in sorted(self.root.glob("*.*.sha1"))
                 if (m := re.fullmatch(r"([\w-]+)\.([A-Za-z0-9-]+)\.sha1", f.name))}
        if not files:
            return {}
        text = ""
        for name in ("README.md", "Makefile", "extract_assets.py"):
            try:
                text += (self.root / name).read_text(errors="replace", encoding="utf-8") + "\n"
            except OSError:
                pass
        if not re.search(r"baserom\.(?:<\w+>|\$\(\w+\)|\{\w+\}|" + "|".join(map(re.escape, files)) + r")\.z64", text):
            return {}
        out = {}
        for version, f in files.items():
            found = re.search(r"\b[0-9a-fA-F]{40}\b", f.read_text(errors="replace", encoding="utf-8"))
            if found:
                out[version] = (f"baserom.{version}.z64", found.group(0).lower())
        return out

    def splat_baseroms(self) -> dict[str, str]:
        """Original ROM hashes for direct versioned splits and generated decompressed images.

        Direct configs supply the original hash. For generated images the Makefile names the input/output
        and the README supplies original hashes; a decompressed-image hash is never a cartridge hash.
        """
        if (getattr(self, "platform", None) or self.detect_platform()) != "n64":
            return {}
        makefile = self.root / "Makefile"
        if not makefile.is_file():
            return {}
        make = makefile.read_text(errors="replace", encoding="utf-8")
        # Direct versioned splits must select the same edition for placement and Make.
        # Require the build's explicit template, not just similarly named YAML files.
        configs = self._splat_version_configs()
        if configs:
            import yaml

            originals = {}
            for version, path in configs.items():
                try:
                    config = yaml.safe_load(path.read_text(encoding="utf-8"))
                except (OSError, ValueError, yaml.YAMLError):
                    return {}
                if not isinstance(config, dict) or not isinstance(config.get("options"), dict):
                    return {}
                options = config["options"]
                from .splatrefs import config_sha1

                digest = config_sha1(path, config)
                base = path.parent / str(options.get("base_path", "."))
                if not (options.get("target_path") == f"baserom.{version}.z64"
                        and base.resolve() == self.root.resolve()
                        and isinstance(digest, str) and re.fullmatch(r"[0-9a-fA-F]{40}", digest)):
                    continue  # (an edition whose split reads a decompressed image has no cartridge hash to offer)
                originals[version] = digest.lower()
            return originals
        if not (re.search(r"^BASEROM\s*[:?+]?=\s*baserom\.\$\(VERSION\)\.z64\s*$", make, re.M) and
                re.search(r"^DECOMPRESSED_BASEROM\s*[:?+]?=\s*decompressed\.\$\(VERSION\)\.z64\s*$", make, re.M)):
            return {}
        readme = self.root / "README.md"
        if not readme.is_file():
            return {}
        text = readme.read_text(errors="replace", encoding="utf-8")
        configured = {p.name.removeprefix("decompressed.").removesuffix(".yaml")
                      for p in self.root.glob("decompressed.*.yaml")}
        return {version: digest.lower() for version, digest in re.findall(
            r"`?baserom\.([A-Za-z0-9._-]+)\.z64`?\s*:\s*`?([0-9a-fA-F]{40})\b", text)
                if version in configured}

    def original_image(self, version: str | None, known: set[str], image_ext: str) -> Path | None:
        """The user's own copy of the game inside the project, when its SHA-1 is one the import recorded.

        `known` holds the hashes that identify the game (what `needs` asks for, the game's identity at
        import). The file `needs` names comes first, then files with the console's image extension at the
        root or one folder down. A file outside the project, or one whose hash isn't known, is never used."""
        if not known:
            return None
        try:
            wanted = self.needs(version).get("path")
        except ForkError:
            wanted = None
        root = self.root.resolve()
        candidates = [self.root / wanted] if wanted and not wanted.endswith("/") else []
        candidates += sorted(self.root.glob("*" + image_ext)) + sorted(self.root.glob("*/*" + image_ext))
        seen = set()
        for path in candidates:
            try:
                real = path.resolve()
                if real in seen or root not in real.parents or not real.is_file():
                    continue
            except OSError:
                continue
            seen.add(real)
            if _file_sha1(real) in known:
                return real
        return None

    def splat_config_for(self, image: Path, version: str | None) -> dict | None:
        """The splat config that splits exactly `image`, read, or None.

        A config that splits another image (a decompressed one built from the cartridge, another release) describes
        other offsets, so it is not used for this one. The config the build names comes first; when it splits
        another file, the project's other splat configs are looked through for the one whose target is `image`
        (projects with one config per release under config/<release>/)."""
        configs = self._splat_version_configs()
        chosen = configs.get(version or "") or (next(iter(configs.values())) if len(configs) == 1 else self._splat_yaml())
        found = self._config_splitting(chosen, image) if chosen else None
        if found is not None:
            return found
        candidates = sorted(self.root.glob("*.yaml")) + sorted(self.root.glob("config/**/*.yaml"))
        for path in candidates[:MAX_SPLAT_CANDIDATES]:
            if path != chosen and (found := self._config_splitting(path, image)) is not None:
                return found
        return None

    @staticmethod
    def _config_splitting(path: Path, image: Path) -> dict | None:
        """The splat config at `path` when its target is `image`, else None."""
        import yaml

        try:
            text = path.read_text(encoding="utf-8")
            if "segments:" not in text or "target_path" not in text:
                return None
            config = yaml.safe_load(text)
        except (OSError, ValueError, yaml.YAMLError):
            return None
        options = config.get("options") if isinstance(config, dict) else None
        if not isinstance(options, dict) or not isinstance(options.get("target_path"), str):
            return None
        try:
            target = (path.parent / str(options.get("base_path", ".")) / options["target_path"]).resolve()
        except OSError:
            return None
        return config if target == image.resolve() else None

    def segments_for(self, image: Path, version: str | None, config: dict | None = None) -> tuple[list[dict], int]:
        """The code segments (and $gp) of the splat config that splits exactly `image`, else none.

        `config` is that config when the caller has already read it (`splat_config_for`)."""
        from ..rom.split import segments_from_config_data

        config = config if config is not None else self.splat_config_for(image, version)
        if config is None:
            return [], 0
        try:
            size = image.stat().st_size
            # A config may name an empty overlay (its next segment starts where it does): it maps nothing.
            segments = [dict(name=s.name, rom_start=s.rom_start, rom_end=s.rom_end, vram=s.vram)
                        for s in segments_from_config_data(config) if 0 <= s.rom_start < s.rom_end <= size]
        except (OSError, TypeError, ValueError, KeyError):
            return [], 0
        gp = config["options"].get("gp_value")
        return segments, gp if isinstance(gp, int) else 0

    def splat_default_version(self) -> str | None:
        versions = self.splat_baseroms()
        if not versions:
            return None
        make = (self.root / "Makefile").read_text(errors="replace", encoding="utf-8")
        match = re.search(r"^VERSION\s*[:?]?=\s*([A-Za-z0-9._-]+)", make, re.M)
        return match.group(1) if match and match.group(1) in versions else next(iter(versions))

    def _docker_only_macos(self) -> bool:
        """Some public decomps explicitly support macOS only through their Dockerfile."""
        if sys.platform != "darwin" or not (self.root / "Dockerfile").is_file():
            return False
        readme = self.root / "README.md"
        return readme.is_file() and bool(re.search(r"macOS.*Docker only|Docker only.*macOS", readme.read_text(
            errors="replace", encoding="utf-8"), re.I | re.S))

    def _docker_image(self) -> str:
        paths = (self.root / "Dockerfile", self.root / "packages.txt")
        digest = hashlib.sha1(str(self.root.resolve()).encode())
        for path in paths:
            if path.is_file():
                digest.update(path.read_bytes())
        return f"decompdecomp-project-{digest.hexdigest()[:16]}"

    def _docker_command(self, command: list[str]) -> list[str]:
        arch = ["--platform", "linux/amd64"] if platform.machine() == "arm64" else []
        return ["docker", "run", "--rm", *arch, "--volume", f"{self.root.resolve()}:/project",
                "--workdir", "/project", "--entrypoint", "/bin/sh", self._docker_image(), "-c",
                shlex.join(command)]

    def _prepare_docker(self, log) -> None:
        with _DOCKER_SETUP_LOCK:
            if getattr(self, "_docker_ready", False):
                return
            if not shutil.which("docker"):
                raise ForkError("this project builds on macOS through Docker, but Docker is not installed. "
                                "Install Docker or a Docker-compatible runtime, then retry setup.")
            ok, tail = self.run(["docker", "info", "--format", "{{.ServerVersion}}"], log=log, timeout=15)
            if not ok:
                raise ForkError("this project needs a running Docker engine on macOS. Start it, then retry setup.\n" + tail)
            image = self._docker_image()
            ok, _ = self.run(["docker", "image", "inspect", image], log=lambda *_: None, timeout=15)
            if not ok:
                arch = ["--platform", "linux/amd64"] if platform.machine() == "arm64" else []
                log("building the project's Docker image with its MIPS toolchain")
                ok, tail = self.run(["docker", "build", *arch, "--tag", image, "."], log=log, timeout=3600)
                if not ok:
                    raise ForkError("the project's Docker image could not be built:\n" + tail)
            if (self.root / ".gitmodules").is_file():
                ok, tail = self.run(["git", "submodule", "update", "--init", "--recursive"], log=log, timeout=900)
                if not ok:
                    raise ForkError("the project's submodules could not be fetched:\n" + tail)
            if ".venv/bin/python3" in (self.root / "Makefile").read_text(errors="replace", encoding="utf-8") and (
                    self.root / "requirements.txt").is_file() and not (self.root / ".venv/bin/python3").exists():
                log("installing the project's Python requirements inside Docker")
                ok, tail = self.run(self._docker_command(["python3", "-m", "venv", ".venv"]), log=log, timeout=600)
                if ok:
                    ok, tail = self.run(self._docker_command([".venv/bin/pip", "install", "-r", "requirements.txt"]),
                                        log=log, timeout=1200)
                if not ok:
                    raise ForkError("the project's Python requirements could not be installed:\n" + tail)
            self._docker_ready = True

    def _inside(self, rel: str) -> str | None:
        """`rel` as a clean path inside the project, or None if it points outside or is a template."""
        if not rel or "__" in rel or "$" in rel or "{" in rel:
            return None
        p = (self.root / rel).resolve()
        if self.root.resolve() not in p.parents:
            return None
        out = Path(os.path.relpath(p, self.root.resolve())).as_posix()
        return out + "/" if rel.endswith("/") else out

    def progress_save(self) -> tuple[Path, dict] | None:
        """A portable save at the root or beside a standalone rebuild, never through links."""
        for folder in (self.root, self.root / "progress"):
            manifest = folder / "decompdecomp.json"
            if not manifest.exists():
                continue
            if folder.is_symlink() or manifest.is_symlink():
                raise ForkError("Saved progress must be a regular folder and manifest, not symbolic links.")
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                # (app/saves.FORMATS: format 2 keeps its notes and verification records in files beside this one)
                if data.get("format") not in (1, 2) or not re.fullmatch(r"[0-9a-fA-F]{40}", data["game"]["sha1"]):
                    raise ValueError("unsupported progress manifest")
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
                raise ForkError(f"Fix the saved progress manifest at {manifest}: {e}") from None
            return folder, data
        return None

    def needs(self, version: str | None = None) -> dict:
        """What the user has to provide: {"what", "path" (None: set up by hand), "sha1", "help", "auto"}."""
        saved = self.progress_save()
        if saved:
            _, manifest = saved
            return {"what": "your copy of the game for this saved progress", "path": "baserom.z64",
                    "sha1": manifest["game"]["sha1"], "auto": True, "saved_progress": True,
                    "help": "decompdecomp will restore the bundled sources, drafts, names, notes and compiler settings, and verify saved matches against your game."}
        kind = self.kind()
        manual = {"what": "your copy of the game", "path": None, "sha1": None, "auto": False,
                  "help": "This project has its own setup. Follow its README in a terminal, in the project folder; "
                          "once its build has produced objdiff.json, click Check again."}
        if kind == "manifest":
            g = self.manifest.get("game", {})
            path = self._inside(g.get("path", ""))
            return {"what": g.get("what", "your copy of the game"), "path": path, "sha1": g.get("sha1"),
                    "help": g.get("help", ""), "auto": path is not None}
        if kind == "dtk":
            v = version or (self.versions()[0] if self.versions() else "")
            return {"what": "your copy of the game's disc (ISO, GCM, RVZ, WIA, WBFS, CISO, NFS or GCZ)",
                    "path": f"orig/{v}/", "sha1": None, "auto": True,
                    "help": f"Version {v}. The build checks the disc's main executable against the project's hash."}
        # (a splat project whose Makefile has no versioned baseroms of its own still names them in decomp.yaml)
        roms = self.decomp_yaml_roms() if kind in ("configure", "custom") or (
            kind == "splat" and not self.splat_baseroms()) else {}
        if roms:
            chosen = version if version in roms else next(iter(roms))
            path, digest = roms[chosen]
            return {"what": f"your original game ROM ({Path(path).name})", "path": path, "sha1": digest, "auto": True,
                    "rom_versions": [{"version": v, "sha1": d} for v, (_, d) in roms.items() if d],
                    "help": "Supply an original cartridge ROM in .z64, .v64 or .n64 byte order; its SHA-1 picks "
                            "the project's version and decompdecomp saves a .z64 copy where the project reads it."}
        if kind in ("configure", "custom") and (named := self.md5_roms()):
            chosen = version if version in named else next(iter(named))
            path, md5s = named[chosen]
            return {"what": f"your original game ROM ({Path(path).name})", "path": path, "sha1": None, "auto": True,
                    "md5": sorted(md5s), "md5_versions": [{"version": v, "md5": sorted(m)} for v, (_, m) in named.items()],
                    "help": "Supply an original cartridge ROM in .z64, .v64 or .n64 byte order; its MD5 picks the project's "
                            "version and decompdecomp saves a .z64 copy where the project reads it."}
        if kind == "configure":
            v = version or ""
            if v and (self.root / "orig" / v).is_dir():
                return {"what": "your copy of the game (see the README for the exact file)", "path": f"orig/{v}/",
                        "sha1": None, "auto": True, "help": "The build checks the game against the project's hashes."}
            if not self._splat_yaml():
                return manual
        if kind in ("splat", "configure"):
            originals = self.splat_baseroms()
            if originals:
                selected = version if version in originals else self.splat_default_version()
                docker_help = (" On macOS, setup uses the project's Dockerfile and requires a running Docker engine."
                               if self._docker_only_macos() else "")
                return {"what": f"your original game ROM (baserom.{selected}.z64)",
                        "path": f"baserom.{selected}.z64", "sha1": originals[selected], "auto": True,
                        "rom_versions": [{"version": v, "sha1": digest} for v, digest in originals.items()],
                        "help": "Supply an original cartridge ROM in .z64, .v64 or .n64 byte order. Its normalized "
                                "SHA-1 selects the build version; decompdecomp saves a .z64 copy for the project. "
                                "The selected version is passed to extraction and subsequent builds."
                                + docker_help}
            import yaml

            config_path = self._splat_yaml()
            try:
                config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, yaml.YAMLError):
                return manual
            options = config.get("options") if isinstance(config, dict) else None
            if not isinstance(options, dict):
                return manual
            target, base = options.get("target_path"), options.get("base_path", ".")
            digest = config.get("sha1", options.get("sha1"))
            sha1 = digest.lower() if isinstance(digest, str) and re.fullmatch(r"[0-9a-fA-F]{40}", digest) else None
            # Use the same effective YAML values as Splat, including the final duplicate key.
            # Nested configs resolve their base against their own directory, not the checkout root.
            path = self._inside(str(config_path.parent / base / target)) if (
                isinstance(target, str) and isinstance(base, str)) else None
            if path is None or path.startswith("build/") or re.search(r"decomp|uncompress", Path(path).name, re.I):
                # (a generated or decompressed image: the original cartridge ROM is named elsewhere)
                return self._makefile_rom(version) or manual
            return {"what": f"your copy of the game ({Path(path).name})", "path": path,
                    "sha1": sha1, "auto": True,
                    "help": ("Checked against the SHA-1 in the project's splat config. " if sha1 else
                             "This splat config does not specify a SHA-1, so decompdecomp cannot check the game "
                             "file by hash before setup. Check the project's README for the required version. ") +
                            "For PlayStation projects, a disc image can supply the expected file."}
        return self._makefile_rom(version) or manual

    # ------------------------------------------------------------------ setup

    def place_game(self, src: Path, filename: str, version: str | None = None, move: bool = False, log=None) -> Path:
        """Put the user's game file where the project expects it, checking its sha1 when the project has one, and
        under the one other name its build script spells out (a decomp.yaml that says paperboy.z64 beside a
        configure.py that reads baserom.z64)."""
        dest = self._place_game(src, filename, version, move, log)
        self._rom_placed = dest
        if dest.suffix.lower() == ".z64" and self.kind() in ("configure", "custom", "splat"):
            names: set[str] = set()
            literal: set[str] = set()  # (several literal names are several versions': never this game under all)
            for script in ("configure.py", "Makefile", "Makefile.common"):
                try:
                    text = (self.root / script).read_text(errors="replace", encoding="utf-8")
                except OSError:
                    continue
                literal |= set(re.findall(r"[\"']?(baserom(?:[.-][A-Za-z0-9]+)*\.z64)[\"']?\b", text))
                if version:  # baserom.$(VERSION).z64 / baserom.{version}.z64: this game's version fills it in
                    names |= {re.sub(r"\$\([^)]*\)|\{[^}]*\}", version, m) for m in re.findall(
                        r"(baserom\.(?:\$\([^)]*\)|\{[^}]*\})\.z64)", text)}
            names |= literal if len(literal) == 1 else set()
            for name in sorted(n for n in names if "$" not in n and "{" not in n)[:4]:
                if name != dest.name and not (self.root / name).exists():
                    try:
                        os.link(dest, self.root / name)
                    except OSError:
                        shutil.copyfile(dest, self.root / name)
        return dest

    def _place_game(self, src: Path, filename: str, version: str | None = None, move: bool = False, log=None) -> Path:
        """Put the user's game file where the project expects it (see place_game)."""
        need = self.needs(version)
        if not need["path"]:
            raise ForkError("decompdecomp doesn't know where this project expects the game; follow its README")
        src = Path(src)
        if not need["path"].endswith(("/", ".z64")):
            from ..rom import RomError

            dest = self.root / need["path"]
            if src.resolve() == dest.resolve():
                if need.get("sha1") and _sha1(src) != need["sha1"]:
                    raise ForkError("The game already in this project does not match its expected SHA-1.")
                return dest
            try:
                for console in self._game_consoles():
                    if console.prepare_fork_game(src, dest, need.get("sha1"), move=move):
                        return dest
            except RomError as error:
                raise ForkError(str(error)) from error
        supplied_sha1 = _sha1(src) if need.get("sha1") or need["path"].endswith(".z64") else None
        details = f"Supplied file SHA-1: {supplied_sha1}"
        normalized_sha1 = None
        if need["path"].endswith(".z64"):
            from ..consoles.n64.rom import copy_z64_file, file_hashes

            hashes = file_hashes(src, supplied_sha1)
            details, normalized_sha1 = hashes.describe(), hashes.normalized_sha1
            if log:
                log(details)
        if need.get("sha1") and supplied_sha1 != need["sha1"]:
            if need["path"].endswith(".z64"):
                if normalized_sha1 == need["sha1"]:
                    dest = self.root / need["path"]
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    copy_z64_file(src, dest)
                    return dest
            inner = self._from_disc(src.read_bytes(), Path(need["path"]).name) if src.stat().st_size < 1 << 30 else None
            if inner is None or hashlib.sha1(inner).hexdigest() != need["sha1"]:
                raise ForkError("That file does not match the version this project expects.\n"
                                f"Expected SHA-1: {need['sha1']}\n{details}\n"
                                "Check the project's README for the required region and revision.")
            dest = self.root / need["path"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(inner)
            return dest
        dest = self.root / need["path"]
        if need["path"].endswith("/"):
            dest.mkdir(parents=True, exist_ok=True)
            dest = dest / (Path(filename).name or "game.iso")
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
        if (need["path"].endswith(".z64") and not need.get("sha1") and normalized_sha1 and normalized_sha1 != supplied_sha1
                and not dest.exists()):  # (a project that names no SHA-1 still reads big-endian: v64/n64 is converted)
            copy_z64_file(src, dest)
            return dest
        # A user may select the game already placed by setup, including through a symlink or hard link.
        # Validate its hash above, then leave it in place instead of copying or moving it onto itself.
        if dest.exists() and src.samefile(dest):
            return dest
        if move:
            shutil.move(str(src), dest)
        else:
            try:
                os.link(src, dest)  # no second copy of a big disc when it's on the same drive
            except OSError:
                shutil.copyfile(src, dest)
        return dest

    @staticmethod
    def _from_disc(data: bytes, name: str) -> bytes | None:
        """A file (e.g. SLUS_013.95) taken from a PlayStation disc image (ISO 9660)."""
        from ..consoles.ps2 import disc

        if not disc.is_disc(data):
            return None
        try:
            return disc._Disc(data).find(name)
        except (ValueError, IndexError, KeyError):
            return None

    def _ld_script_missing(self, config: Path) -> bool:
        """Does the splat config name a linker script (written by the split) that isn't there yet?"""
        import yaml

        try:
            options = (yaml.safe_load(config.read_text(encoding="utf-8")) or {}).get("options") or {}
        except (OSError, ValueError, yaml.YAMLError, AttributeError):
            return False
        name = options.get("ld_script_path")
        rel = self._inside(str(config.parent / options.get("base_path", ".") / name)) if isinstance(name, str) else None
        return bool(rel) and not (self.root / rel).exists()

    def setup_commands(self, version: str | None = None) -> list[list[str]]:
        """The commands that set the project up for a version of its game, as its kind of build does it."""
        kind = self.kind()
        if kind == "manifest":
            m = self.manifest
            cmds = m.get("setup", [])
            if m.get("created") == "from scratch" and cmds:  # point at this computer's tools, not the creator's
                from .. import consoles

                args = consoles.get(m.get("platform")).tool_args()
                first = cmds[0]
                keep = next((i for i, c in enumerate(first) if c.startswith("--")), len(first))
                cmds = [[*first[:keep], *args], *cmds[1:]]
            return [[sys.executable if c in ("python", "python3") else c for c in cmd] for cmd in cmds]
        if kind == "dtk":
            cmd = [sys.executable, "configure.py"]
            if version and len(self.versions()) > 1:
                cmd += ["--version", version]
            return [cmd, [_tool("ninja") or "ninja"]]
        if kind == "configure":
            flags = self._configure_flags()
            steps = []
            if "--setup" in flags:  # the project's own download of its compiler, as its README says to run first
                steps.append(["python", "configure.py", "--setup"])
            cmd = ["python", "configure.py"]  # (its own environment's python, if any)
            if "--split" in flags:  # splitting is a flag here; plain configure.py then only writes nothing
                cmd.append("--split")
            if version and "--version" in flags and version in self.rom_originals():
                cmd += ["--version", version]
            return [*steps, cmd, [_tool("ninja") or "ninja"]]
        if kind == "custom" and not (self.root / "Makefile").is_file() and (self.root / "configure").is_file():
            # `./configure` (a script that writes build.ninja from the original's splat output), then ninja
            return [[str(self.root / "configure")], [_tool("ninja") or "ninja"]]
        if kind == "custom" and (self.root / "Makefile").is_file():
            # no splat config at the root, but a Makefile with the usual targets (setup, extract, then the build)
            text = (self.root / "Makefile").read_text(errors="replace", encoding="utf-8")
            chosen = [f"{var}={_makefile_version_word(text, var, version)}"] if version and (
                var := _version_var(text)) else []
            if version and re.fullmatch(r"[a-z]{2,3}\.v[0-9][\w.-]*", version, re.I) and re.search(r"^REGION\s*[:?]?=", text, re.M):
                chosen = ["REGION=" + version.split(".")[0], "VERSION=" + version.split(".", 1)[1]]  # (us.v77)
            steps = [["make", *chosen, t] for t in _setup_targets(text)]
            if not steps and re.search(r"^split\s*:", text, re.M):  # (splat's output: *_auto.txt the link reads)
                steps = [["make", *chosen, "split"]]
            return steps + [["make", *chosen, f"-j{os.cpu_count() or 4}", *_real_default_goal(text)]]
        if kind == "splat":
            make = (self.root / "Makefile").read_text(errors="replace", encoding="utf-8")
            chosen = [f"VERSION={version}"] if version and self.splat_baseroms() else []
            steps = [["make", *chosen, t] for t in _setup_targets(make)]
            config = self._splat_yaml()
            splitter = next((s for s in (self.root / "tools" / "splat" / "split.py", self.root / "splat" / "split.py")
                             if s.is_file()), None)
            # the directories its Makefile links binary pieces from (assets/*.bin, bin/*.bin) that only a split writes
            wanted = [d for d in ("assets", "bin") if re.search(rf"(?<![\w/.-]){d}/|BIN_DIRS\s*[:?]?=\s*[^\n]*\b{d}\b", make)]
            if (not steps and config and splitter and wanted and not any((self.root / d).exists() for d in wanted)
                    and not re.search(r"^(?:split|setup|extract)\s*:", make, re.M)):
                # a project whose README says "run splat" before `make`: the split writes those pieces
                steps = [["python", str(splitter.relative_to(self.root)), config.name]]
            elif (not steps and config and not splitter and self._ld_script_missing(config)
                    and not re.search(r"^(?:split|setup|extract)\s*:", make, re.M)):
                # the same, for a project that names no splat of its own: the one decompdecomp runs from
                steps = [["python", "-m", "splat", "split", config.name]]
            jobs = min(os.cpu_count() or 4, 4) if self._docker_only_macos() else os.cpu_count() or 4
            return steps + [["make", *chosen, f"-j{jobs}", *_real_default_goal(make)]]
        if kind == "objdiff":
            cfg = self.objdiff_config()
            return [[cfg.get("custom_make") or "make", *cfg.get("custom_args", [])]]
        raise ForkError("decompdecomp can't tell how to build this project; follow its README, then refresh")

    def run(self, cmd: list[str], log=print, timeout: float = 3600, failed: list[str] | None = None,
            platform: str | None = None) -> tuple[bool, str]:
        """Run a build step in the project. Targets the build reports as failed (ninja's FAILED:, make's Error lines)
        are added to `failed`. `platform`: its console, for a project whose manifest isn't written yet (its import's
        first build: a game built with Visual C++ 2017 then built without wine's settings)."""
        code, out, hints = self._execute(cmd, log, timeout, failed, platform)
        # what failed, not the warnings after it: dsd's hundreds of "[WARN ] No module for relocation" lines came after
        # a compiler's error and pushed it out of the tail agents and the user read
        said = [ln for ln in out if not _NOISE.match(ln) and not ln.lstrip().startswith("[WARN")] or out
        problems = [line for line in said if _BUILD_ERROR.search(line)] if code else []
        tail = "\n".join(dict.fromkeys([*problems[:12], *said[-60:]]))
        if hints:
            tail = hints + "\n\nBuild output:\n" + tail
        return code == 0, tail

    def _execute(self, cmd: list[str], log, timeout: float, failed: list[str] | None,
                 platform: str | None = None) -> tuple[int | None, list[str], str]:
        """Run a build step: (its exit code, None when it couldn't start or ran out of time; every line it printed,
        or why it didn't run; what buildhelp.explain makes of a failure, or ""). `platform`: as in run. The time
        limit holds while the step still prints, or hangs with its output open, and stops it with everything it
        started: its output used to be read to the end before the limit was looked at, so a compiler that never
        finished on an agent's code (stuck under wibo, or CodeWarrior looping on one #pragma) kept the build, and
        the project's build lock, for hours (#114, whose fix this is)."""
        console = consoles.get((self.manifest or {}).get("platform") or platform or getattr(self, "platform", None)
                               or self.detect_platform())
        if self._docker_only_macos() and Path(cmd[0]).name in ("make", "gmake"):
            self._prepare_docker(log)
            cmd = self._docker_command(["make", *cmd[1:]])
        else:
            cmd = console.project_build_command(self, cmd)
        if Path(cmd[0]).name == "make" and sys.platform == "darwin" and (gnu := shutil.which("gmake")):
            cmd = [gnu, *cmd[1:]]  # macOS's make is 3.81: public Makefiles use what GNU make 4 added
        env = dict(os.environ)
        # Existing project environments carry its pinned extractor dependencies. Do not install into the app.
        python = next((folder / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python3')
                       for folder in (self.root / '.venv', self.root / 'venv')
                       if (folder / 'pyvenv.cfg').is_file() and
                       (folder / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python3')).is_file()), None)
        project_python = [python.parent] if python else []
        if python:
            env['VIRTUAL_ENV'] = str(python.parent.parent)
        project_tools = console.project_tool_paths(self)
        env.update(console.build_env())  # what its console's builds need (Console.build_env)
        cargo = Path.home() / ".cargo" / "bin"  # (what `cargo install` put there: pigment64, crunch64's CLI, ...)
        env["PATH"] = os.pathsep.join([*(str(d) for d in [*project_python, *_tool_dirs(), *project_tools]),
                                       env.get("PATH", ""), *([str(cargo)] if cargo.is_dir() else [])])
        if sys.platform == "darwin":  # Homebrew's libraries (fmt, libpng, ...) for the host tools a project builds
            for prefix in ("/opt/homebrew", "/usr/local"):
                if (Path(prefix) / "lib").is_dir() and (Path(prefix) / "include").is_dir():
                    env["LIBRARY_PATH"] = os.pathsep.join(filter(None, [env.get("LIBRARY_PATH"), f"{prefix}/lib"]))
                    env["CPATH"] = os.pathsep.join(filter(None, [env.get("CPATH"), f"{prefix}/include"]))
                    break
        if sys.platform == "darwin" and (self.root / "tools").is_dir():
            env.update({k: v for k, v in _mac_cxx_env(self.root).items() if k not in env})
        if sys.platform == "darwin" and (self.root / "tools").is_dir() and (stand_in := _mac_host_headers(self.root)):
            env["CPATH"] = os.pathsep.join(filter(None, [env.get("CPATH"), str(stand_in)]))
        if sys.platform == "darwin" and Path(cmd[0]).name in ("make", "gmake") and not any(a.startswith("ICONV=") for a in cmd):
            iconv = _iconv_override(self.root, project_tools, env["PATH"])
            if iconv:
                cmd = [*cmd, f"ICONV={iconv}"]
        env["NINJA_STATUS"] = "[%f/%t] "  # (ninja's own: its progress lines are what a run of units is read by)
        # found on that PATH (decompdecomp's own ninja): Linux looks a command up there itself, Windows only in this
        # process's PATH, where decompdecomp's environment isn't when it's installed
        executable = str(python or sys.executable) if cmd[0] in ("python", "python3") else shutil.which(cmd[0], path=env["PATH"]) or cmd[0]
        cmd = [console_python(executable), *cmd[1:]]  # not pythonw.exe: its tools' compilers would flash consoles
        log("$ " + " ".join(Path(c).name if i == 0 else c for i, c in enumerate(cmd)))
        try:
            # (in a process group of its own off Windows, so a step stopped at its limit takes its compilers with it)
            proc = subprocess.Popen(capped(cmd), cwd=self.root, env=env, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, errors="replace", encoding="utf-8",
                                    creationflags=NO_WINDOW, start_new_session=os.name != "nt")
        except FileNotFoundError:
            return None, [f"{cmd[0]} isn't installed"], ""
        from .. import compiler_processes
        compiler_processes.track(proc.pid)
        attach_job(proc)  # (Windows: its commands are stopped with it, even once it's gone; elsewhere its session)
        out, seen, quiet = [], collections.Counter(), [0]
        raised: list[BaseException] = []  # what `log` (or `failed`'s caller) raised in the reader: the build's to raise

        def read() -> None:
            try:
                for line in proc.stdout:
                    line = line.rstrip()
                    out.append(line)
                    if failed is not None and (m := _FAILED.match(line)):
                        failed.extend(t for t in (m.group(1) or m.group(2)).split()
                                      if t not in failed and not t.startswith("[code="))
                    seen[line] += 1
                    if seen[line] > 2 or _NOISE.match(line):  # the same line again, or CodeWarrior's linker chatter
                        quiet[0] += 1
                    elif len(out) <= 400 or len(out) % 50 == 0 or _BUILD_ERROR.search(line):
                        log(line)
            except (ValueError, OSError):  # (its output closed under it by the cleanup: what was read stands)
                pass
            except BaseException as e:  # a cancellation from the caller's log: stops the build, raised by it
                raised.append(e)

        reader = threading.Thread(target=read, daemon=True, name="build-output")
        reader.start()
        deadline = time.monotonic() + timeout
        primary_failure = True
        code = None
        watchdogs: list = []

        def watched() -> set[int]:
            return set().union(*(getattr(w, "seen", ()) for w in watchdogs))

        def stop() -> None:
            stop_group(proc, watched())  # what's still in its session or job, its own parent gone too
            _stop_tree(proc)  # ninja and the compiler it started: killing ninja alone left the compiler running
            try:
                proc.kill()  # (a refused group signal leaves the step itself)
            except OSError:
                pass

        def timed_out() -> tuple[None, list[str], str]:
            reader.join(OUTPUT_SECONDS)
            said = [ln for ln in list(out) if not _NOISE.match(ln)][-40:]
            return None, [*said, f"stopped: Build timed out after {timeout:g}s: it didn't finish in {timeout:g} "
                                 "seconds, so it was stopped with everything it started (its compiler processes "
                                 "too). A compiler that never finishes on this code is the usual cause: change the "
                                 "code that was just added."], ""

        try:
            # macOS: each process's memory (DECOMP_COMPILER_MEMORY_MB, as for a compile) and the whole build's, as its
            # systemd scope caps it on Linux (limits.py's docstring). The time limit is this loop's, not a watchdog's:
            # stop() reads the build's tree before stopping ninja, whose commands in groups of their own are found
            # only from it
            with ExitStack() as stack:
                watchdogs += [stack.enter_context(CompilerMemoryWatchdog(proc)),
                              stack.enter_context(CompilerMemoryWatchdog(proc, limit=default_cap(), whole_tree=True))]
                while True:
                    try:
                        code = proc.wait(timeout=0.1)
                        break
                    except subprocess.TimeoutExpired:
                        if raised:
                            stop()
                            raise raised[0] from None
                        if time.monotonic() >= deadline:
                            stop()
                            return timed_out()
            if raised:
                raise raised[0]
            if any(getattr(w, "timed_out", False) for w in watchdogs):
                return timed_out()
            # (the step is over: its output ends with it, or soon after; the time limit still holds meanwhile)
            reader.join(min(OUTPUT_SECONDS, max(0.0, deadline - time.monotonic())))
            if reader.is_alive():
                if time.monotonic() >= deadline:  # what it started kept its output open past the time limit
                    code = None
                    stop()
                    return timed_out()
                # The step ended, but something it started still holds its output: stopped, so it doesn't run on
                # with nobody waiting for it; the step's own exit code stands (#228: a build that succeeded was
                # waited on for the rest of its hour, then failed as out of time).
                stop_group(proc, watched())
                reader.join(OUTPUT_SECONDS)
            if raised:
                raise raised[0]
            primary_failure = any(getattr(w, "exceeded", False) for w in watchdogs)
        finally:
            release_job(proc)
            compiler_processes.finish(proc, failed=primary_failure or code not in (None, 0))
        if (over := next((w for w in watchdogs if getattr(w, "exceeded", False)), None)) is not None:
            # The process may exit normally while the watchdog's signal races with it (or is denied). Crossing the
            # limit still fails the build.
            code = code or 1
            out.append(over.message())
        lines = list(out)  # (what was read: a reader still going adds to its own list, not this one)
        if quiet[0]:
            log(f"({quiet[0]} repeated lines and linker warnings not shown)")
        if killed_for_memory(code):
            lines.append("stopped: it used more memory than decompdecomp allows a single tool, so it was stopped "
                         "before it could slow down or freeze your computer")
        hints = ""
        if code:
            from .buildhelp import explain

            if hints := explain(self.root, executable, "\n".join(lines)):
                log(hints)
        return code, lines, hints

    def unpack_disc(self, version: str | None, log=print) -> int:
        """A dtk project that reads an extracted disc (orig/<version>/sys/main.dol, files/...) instead of the disc
        image: copy the files its config names out of the user's disc image with decompdecomp's own dtk, as the README's
        "extract the disc with Dolphin" step would. Returns how many files were copied."""
        if self.kind() != "dtk" or not version:
            return 0
        cfg, orig = self.root / "config" / version / "config.yml", self.root / "orig" / version
        if not cfg.is_file() or not orig.is_dir():
            return 0
        text = cfg.read_text(errors="replace", encoding="utf-8")
        if re.search(r"^\s*object_base\s*:", text, re.M):
            return 0  # this dtk reads the disc image itself
        wanted = sorted({m.group(1) for m in re.finditer(rf"orig/{re.escape(version)}/([^\s'\"#,\]]+)", text)})
        missing = [w for w in wanted if not w.endswith("/") and ".." not in w and not (orig / w).exists()]
        if not missing:
            return 0
        from ..consoles.gc_wii import scratch as dtk  # dtk projects are GameCube and Wii ones

        discs = [(p, dtk.disc_id(p)) for p in sorted(orig.iterdir()) if p.is_file() and p.stat().st_size > 1 << 20]
        discs = [(p, gid) for p, gid in discs if gid]
        disc = next((p for p, gid in discs if gid == version), None) or (discs[0][0] if len(discs) == 1 else None)
        if disc is None:
            return 0  # no disc image here (or only other versions): the project's own error says what's missing
        log(f"taking the {len(missing)} files the project reads out of your disc image ({disc.name})")
        by_dir: dict[str, list[str]] = {}
        for w in missing:
            by_dir.setdefault(str(Path(w).parent), []).append(w)
        for parent, files in by_dir.items():
            dtk.vfs_copy(disc, files, orig / parent)
        return len(missing)

    # Python packages whose command line tools public decomps call without listing them in their requirements
    PYTHON_TOOLS = {"pigment64": "pigment64", "crunch64": "crunch64", "mapfile_parser": "mapfile-parser",
                    "n64img": "n64img", "spimdisasm": "spimdisasm", "splat64": "splat64[mips]", "m2c": "m2c",
                    "ninja_syntax": "ninja-syntax", "rabbitizer": "rabbitizer", "pyyaml": "pyyaml",
                    "pypng": "pypng", "tqdm": "tqdm", "colorama": "colorama", "capstone": "capstone",
                    "pygfxd": "pygfxd", "intervaltree": "intervaltree", "pylibyaml": "pylibyaml",
                    "yaml": "pyyaml", "git": "gitpython", "splat": "splat64[mips]", "png": "pypng",
                    "requests": "requests", "attr": "attrs", "PIL": "pillow", "elftools": "pyelftools",
                    "zstandard": "zstandard"}

    def _install_missing_tools(self, tail: str, log) -> bool:
        """A build that stops on `<tool>: command not found` (or a missing Python module) for a package from
        PYTHON_TOOLS installs it into the project's own environment: True when something was installed, so the
        step can run again."""
        wanted = {m.group(1) for m in re.finditer(r"([\w-]+): (?:command )?not found", tail)}
        wanted |= {m.group(1) for m in re.finditer(r"No module named '([\w.]+)'", tail)}
        if "code=127" in tail or "not found" in tail:  # (ninja's report shows the command line that failed)
            wanted |= {name for name in self.PYTHON_TOOLS if re.search(rf"^{re.escape(name)} ", tail, re.M)}
        packages = sorted({self.PYTHON_TOOLS[w.split(".")[0]] for w in wanted if w.split(".")[0] in self.PYTHON_TOOLS})
        python, uv = self._project_python(), _tool("uv")
        if not packages or python is None or not uv:
            return False
        log(f"the build needs {', '.join(packages)}: installing it into the project's Python environment")
        ok, _ = self.run([uv, "pip", "install", "--python", str(python), *packages], log=log, timeout=900)
        return ok

    def _configure_flags(self) -> set[str]:
        """The long options the project's configure.py accepts, from its own --help (empty when it can't say)."""
        try:
            cmd = [str(self._project_python() or sys.executable), "configure.py", "--help"]
            res = subprocess.run(cmd, cwd=self.root, capture_output=True, text=True, timeout=60, errors="replace",
                                 encoding="utf-8", stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            return set()
        return set(re.findall(r"(?<![\w-])(--[a-z][a-z0-9-]*)", res.stdout + res.stderr))

    def _project_python(self) -> Path | None:
        return next((folder / ("Scripts/python.exe" if os.name == "nt" else "bin/python3")
                     for folder in (self.root / ".venv", self.root / "venv")
                     if (folder / "pyvenv.cfg").is_file()
                     and (folder / ("Scripts/python.exe" if os.name == "nt" else "bin/python3")).is_file()), None)

    # a public tool's sources a project's build calls by path without carrying them or any script that fetches them:
    # (folder as the build names it, repository, the commit fetched)
    KNOWN_TOOL_REPOS = (
        ("asm-processor", "https://github.com/simonlindholm/asm-processor", "f3b2f85a5bc7e8ae2030ea5a83ea8aa0bcbede03"),
    )

    def _fetch_known_tool_repos(self, log) -> None:
        """`python3 tools/asm-processor/build.py ...` in a project's configure.py or Makefile, with no such folder, no
        submodule for it and no setup script: the repository the whole community uses, at one pinned commit."""
        text = ""
        for name in ("configure.py", "Makefile", "Makefile.common"):
            try:
                text += (self.root / name).read_text(errors="replace", encoding="utf-8") + "\n"
            except OSError:
                pass
        try:
            modules = (self.root / ".gitmodules").read_text(errors="replace", encoding="utf-8")
        except OSError:
            modules = ""
        for folder, url, commit in self.KNOWN_TOOL_REPOS:
            where = next((p for p in (f"tools/{folder}", folder) if re.search(
                rf"(?<![\w/.-])(?:\./)?{re.escape(p)}/build\.py", text)), None)
            if not where or (self.root / where).exists() or where in modules:
                continue
            log(f"fetching {url} into {where} (the build runs it from there), at {commit[:10]}")
            ok, tail = self.run(["git", "clone", "-q", url + ".git", where], log=log, timeout=900)
            if ok:
                ok, tail = self.run(["git", "-C", where, "checkout", "-q", commit], log=log, timeout=300)
            if not ok:
                shutil.rmtree(self.root / where, ignore_errors=True)
                log(f"could not fetch {url}: {tail[-300:]}")

    def build_setup_script_host_tools(self, log=print) -> None:
        """A tools/setup.sh that compiles a small host program its Makefile then runs
        (`gcc -o tools/build/mkldscript tools/build/mkldscript.c tools/build/spec.c ...`): that one command, with the
        host's C compiler, when the program isn't built yet. Only a plain `gcc|cc -o <out> <sources>.c` line of the
        script is read, never run as a script."""
        cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
        if self.kind() == "manifest" or not cc:
            return
        for name in ("tools/setup.sh", "setup.sh"):
            try:
                script = (self.root / name).read_text(errors="replace", encoding="utf-8")
            except OSError:
                continue
            for out, sources in re.findall(r"^\s*(?:gcc|cc)\s+-o\s+([\w./-]+)\s+((?:[\w./-]+\.c\s*)+)$", script, re.M):
                target, inputs = self._inside(out), [self._inside(src) for src in sources.split()]
                if not target or None in inputs or (self.root / target).exists():
                    continue
                if not all((self.root / src).is_file() for src in inputs):
                    continue
                log(f"building {target} with the host's C compiler, as the project's setup script does")
                ok, tail = self.run([cc, "-o", target, *inputs], log=log, timeout=600)
                if not ok:
                    log(f"could not build {target}: {tail[-300:]}")

    def fetch_pinned_tool_repos(self, log=print) -> None:
        """A project whose own tools/setup.sh clones a public repository under tools/ (an SDK's sources pinned to a
        commit) that its Makefile then reads: clone that one, at that commit, when the folder is missing. Only
        `git clone` / `git -C <dir> checkout <commit>` lines of that script are read, never run as a script."""
        if self.kind() == "manifest" or not shutil.which("git"):
            return
        self._fetch_known_tool_repos(log)
        make = (self.root / "Makefile").read_text(errors="replace", encoding="utf-8") if (
            self.root / "Makefile").is_file() else ""
        for name in ("tools/setup.sh", "setup.sh"):
            try:
                script = (self.root / name).read_text(errors="replace", encoding="utf-8")
            except OSError:
                continue
            base = Path(name).parent
            clones = re.findall(
                r"git clone\s+(?:-\S+\s+)*(https://github\.com/[\w.-]+/[\w.-]+?)(?:\.git)?(?:\s+([\w.-]+))?(?=[\s;&|]|$)",
                script)
            for variable, owners in re.findall(r"for\s+(\w+)\s+in\s+([\w./ -]+?)\s*;\s*do\b[\s\S]{0,300}?github\.com/\$\{?\1\b", script):
                clones += [("https://github.com/" + owner, "") for owner in owners.split() if owner.count("/") == 1]
            for url, folder in clones:
                folder = folder or url.rsplit("/", 1)[1]
                target = base / folder
                if (self.root / target).exists() or folder.startswith(".") or "/" in folder:
                    continue
                if not re.search(rf"(?<![\w.-]){re.escape(target.as_posix())}\b|\bULTRALIB\b.*{re.escape(folder)}", make):
                    continue
                pinned = re.search(rf"git -C {re.escape(folder)} checkout\s+(?:-\S+\s+)*([0-9a-f]{{40}})", script)
                log(f"fetching {url} into {target}, as the project's setup script does")
                ok, tail = self.run(["git", "clone", "-q", url + ".git", target.as_posix()], log=log, timeout=900)
                if ok and pinned:
                    ok, tail = self.run(["git", "-C", target.as_posix(), "checkout", "-q", pinned[1]], log=log, timeout=300)
                if not ok:
                    shutil.rmtree(self.root / target, ignore_errors=True)
                    log(f"could not fetch {url}: {tail[-300:]}")

    def ensure_python_env(self, log=print) -> None:
        """Give a project its own Python environment when it lists requirements and has none: its configure.py and
        splat run in it (`_execute` puts a `.venv` or `venv` first on PATH), as `pip install -r requirements.txt`
        in a README would. Never installs into decompdecomp's own Python. A failure is logged, not raised: the
        step that needed the package then says what's missing."""
        if self.kind() == "manifest" or self._docker_only_macos():
            return
        if any((self.root / folder / "pyvenv.cfg").is_file() for folder in (".venv", "venv")):
            return
        requirements, pyproject = self.root / "requirements.txt", self.root / "pyproject.toml"
        if not requirements.is_file():  # (where some projects keep theirs)
            requirements = next((p for name in ("requirements.txt", "requirements-python.txt")
                                 if (p := self.root / "tools" / name).is_file()), requirements)
        if not requirements.is_file() and (self.root / "tools/configure/requirements.txt").is_file():
            requirements = self.root / "tools/configure/requirements.txt"  # (a `./configure` that is a script of its own)
        bundled = None
        if not requirements.is_file() and not (pyproject.is_file() and re.search(
                r"^dependencies\s*=\s*\[\s*\S", pyproject.read_text(errors="replace", encoding="utf-8"), re.M)):
            # (a project that carries its own splat: that splat's requirements, as of the day it was committed)
            bundled = next((p for p in (self.root / "tools/splat/requirements.txt", self.root / "splat/requirements.txt")
                            if p.is_file()), None)
            requirements = bundled or requirements
        needs = requirements.relative_to(self.root).as_posix()
        # The version of splat the project's Dockerfile installs: its extensions are written for that one, and a
        # requirements file that says only "<1.0" would install a newer splat they can't load into
        pins = []
        try:
            docker = (self.root / "Dockerfile").read_text(errors="replace", encoding="utf-8")
            pins = [f"splat64{extra}{spec}" for extra, spec in re.findall(
                r"[\"']?splat64(\[[\w,]+\])?\s*([<>=!~]=?[\w.]+)[\"']?", docker)][:1]
        except OSError:
            pass
        uses_pyproject = False
        if pyproject.is_file():
            text = pyproject.read_text(errors="replace", encoding="utf-8")
            uses_pyproject = bool(re.search(r"^dependencies\s*=\s*\[\s*\S", text, re.M))
        if not requirements.is_file() and not uses_pyproject:
            return
        uv = _tool("uv")
        # a Makefile that runs `venv/bin/python3` names the folder: use that one, else the usual .venv
        make = (self.root / "Makefile").read_text(errors="replace", encoding="utf-8") if (
            self.root / "Makefile").is_file() else ""
        folder = "venv" if re.search(r"(?<![.\w])venv/bin/", make) else ".venv"
        log(f"installing the project's Python requirements into its own environment ({folder})")
        python = str(self.root / folder / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
        if uv and uses_pyproject and (self.root / "uv.lock").is_file():
            # (UV_PROJECT_ENVIRONMENT would otherwise send the sync into the environment decompdecomp runs from)
            steps = [[*(["env", f"UV_PROJECT_ENVIRONMENT={self.root / folder}"] if os.name != "nt" else []),
                      uv, "sync", "--python", sys.executable]]
        elif uv:
            steps = [[uv, "venv", "--seed", "--python", sys.executable, str(self.root / folder)],
                     [uv, "pip", "install", "--python", python, "-r", needs, *pins] if requirements.is_file()
                     else [uv, "pip", "install", "--python", python, "."]]
        else:
            steps = [[sys.executable, "-m", "venv", str(self.root / folder)]]
            if requirements.is_file():
                steps.append([python, "-m", "pip", "install", "-r", needs, *pins])
        for step in steps:
            era = bundled is not None and uv and step[0] == uv and "install" in step
            # the bundled splat is written for the spimdisasm of its day (a newer one writes assembly its macros
            # don't define): the newest packages as of that day, or the first later day that builds on this Python,
            # and only then what the requirements ask for today
            for date in [*(self._era_dates(bundled.parent) if era else []), None]:
                if date:
                    log(f"installing the packages as of {date}, the era of the project's own splat")
                ok, tail = self.run([*step[:5], "--exclude-newer", date, *step[5:]] if date else step,
                                    log=log, timeout=1800)
                if ok:
                    break
            if not ok:
                log(f"the project's Python requirements could not be installed: {tail[-400:]}")
                return
        try:  # (its environment is not part of its sources)
            exclude = self.root / ".git" / "info" / "exclude"
            if exclude.parent.is_dir() and (not exclude.exists() or folder not in exclude.read_text(
                    errors="replace", encoding="utf-8")):
                with open(exclude, "a", encoding="utf-8") as f:
                    f.write(f"\n{folder}/\n")
        except OSError:
            pass

    def _era_dates(self, folder: Path) -> list[str]:
        """The day the project last changed `folder` (its bundled splat), then later days, as `uv --exclude-newer`
        dates: packages from before a Python's wheels exist can't be built, so each later date is a fallback."""
        ok, out = self.git("log", "-1", "--format=%cs", "--", folder.relative_to(self.root).as_posix())
        try:
            day = datetime.date.fromisoformat(out.strip().splitlines()[-1]) if ok and out.strip() else None
        except ValueError:
            day = None
        if day is None:
            return []
        return [(day + datetime.timedelta(days=n)).isoformat() for n in (1, 120, 240, 480)
                if day + datetime.timedelta(days=n) <= datetime.date.today()]

    def setup(self, version: str | None = None, log=print) -> None:
        """Set the project up for the user's copy of the game: unpack it, run the setup commands, and build
        every source once so objdiff can measure each unit."""
        self.unpack_disc(version, log=log)
        self.ensure_python_env(log=log)
        self.fetch_pinned_tool_repos(log=log)
        self.build_setup_script_host_tools(log=log)
        self._drop_stale_splat_cache()
        for cmd in self.setup_commands(version):
            seen: list[str] = []

            def tee(*parts, _seen=seen):
                _seen.append(" ".join(str(p) for p in parts))
                log(*parts)

            ok, tail = self.run(cmd, log=tee)
            if not ok and (missing := re.search(r"No rule to make target [`'‘](baserom[\w.-]*\.z64)['’]", tail)) \
                    and getattr(self, "_rom_placed", None) and not (self.root / missing.group(1)).exists():
                # the Makefile reads the ROM under a name nothing else said: that name, then, for this game
                try:
                    os.link(self._rom_placed, self.root / missing.group(1))
                except OSError:
                    shutil.copyfile(self._rom_placed, self.root / missing.group(1))
                log(f"the project reads the game as {missing.group(1)}: saved it under that name too")
                ok, tail = self.run(cmd, log=log)
            if (not ok and any(Path(part).name == "split.py" for part in cmd[:3])
                    and "assert(unk_size + rest_size == total_size)" in tail):
                # an old bundled splat's closing statistics check fails when its yaml leaves part of the game
                # unmapped, after it has written every piece the build reads
                log("the bundled splat stopped at its closing statistics check; its split files are written")
                ok = True
            if not ok and re.search(r"cannot execute binary file|Exec format error|Permission denied", tail):
                # a program the step itself fetched (a submodule's committed Linux compiler): the console's hook
                # looks at the tree again before every command, so the step runs once more
                ok, tail = self.run(cmd, log=log)
            if not ok and re.search(r"\*\*\* \[[^\]]*: clean\] Error", "\n".join(seen)) and any(a.startswith("-j") for a in cmd):
                # a default target that lists `clean` beside the build runs both at once under -j (rm fails on the
                # directory the build is filling): one job at a time it works
                log("the build raced with its own clean step; running it again one job at a time")
                cmd = [a for a in cmd if not a.startswith("-j")] + ["-j1"]
                ok, tail = self.run(cmd, log=log)
            if (not ok and Path(cmd[0]).name in ("make", "gmake") and any(a.startswith("-j") and a != "-j1" for a in cmd)
                    and re.search(r"undefined reference to|mkdir: [^\n]*File exists|No such file or directory", "\n".join(seen))):
                # Makefiles that assume their steps run in order (a copy into a folder another job makes, a link that
                # needs a sibling's output) lose that race under -j on a first build; what the first pass built stays
                # built, so the build run again one job at a time finishes it. A folder-shaped name that a raced
                # copy left as a file in the build folder is removed first.
                for name in re.findall(r"mkdir: (\S+): File exists", "\n".join(seen)):
                    stale = (self.root / name).resolve()
                    if stale.is_file() and (self.root / "build").resolve() in stale.parents:
                        stale.unlink()
                log("the build lost a race between its own steps; running it again")
                # (-k first: one that picked a link rule before the sibling file it needs was built has that file
                # built by the other targets now; then the plain build, one job at a time, finishes what is left)
                failed_targets: list[str] = []
                ok, tail = self.run([*cmd, "-k"], log=log, failed=failed_targets)
                asked: set[str] = set()
                for _ in range(8):  # (each pass reaches the targets that waited on the last one's)
                    if ok or not (fresh := [t for t in failed_targets if t not in asked]):
                        break
                    # GNU make 4.4 picks the generic `%.elf` rule over a project's own, more specific one (with
                    # target-specific variables it second-expands) for a target it only reaches as an intermediate
                    # (Super Mario 64's leveldata.elf), where 4.3 picked the specific one; asked for by name, the
                    # target takes its own rule. So what failed is asked for first, then the build runs again.
                    asked.update(fresh)
                    flags = [a for a in cmd[1:] if a.startswith("-") or "=" in a]
                    self.run([cmd[0], *flags, "-k", *fresh[:400]], log=log)
                    failed_targets = []
                    ok, tail = self.run([*cmd, "-k"], log=log, failed=failed_targets)
                if not ok:
                    cmd = [a for a in cmd if not a.startswith("-j")] + ["-j1"]
                    ok, tail = self.run(cmd, log=log)
            for _ in range(6):  # (each run names the next missing package)
                if ok or not self._install_missing_tools(tail, log):
                    break
                ok, tail = self.run(cmd, log=log)
            for _ in range(4):  # (the link names one missing list at a time)
                auto = re.findall(r"cannot open linker script file (\S+_auto\.txt)", tail) if not ok else []
                made = False
                for name in dict.fromkeys(auto):
                    # splat's "auto" symbol lists the link reads: empty, when the project's split didn't write them
                    if (target := self._inside(name)) and not (self.root / target).exists():
                        (self.root / target).write_text("", encoding="utf-8")
                        made = True
                if not made:
                    break
                ok, tail = self.run(cmd, log=log)
            make_step = Path(cmd[0]).name.removesuffix('.exe') in ('make', 'gmake')
            if (not ok and make_step and not (self.root / 'objdiff.json').exists()
                    and re.search(r'computed checksum did NOT match|build succeeded, but did not match', tail)):
                # A linked but incomplete decomp can still be measured. The generator must independently
                # verify every original reference section; compiler and missing-tool failures keep failing.
                log('The whole-image check failed; trying independently verified original references.')
                try:
                    self.generate_objdiff(version=version, log=log)
                except ForkError as error:
                    log(f'Original references could not be verified: {error}')
            # Incomplete sources may fail make once objdiff can measure individual units. Without that
            # configuration, a failed build is a setup failure, not a successful build missing its report.
            if not ok and (not make_step or not (self.root / "objdiff.json").exists()):
                if mismatch := _hash_mismatch(tail):
                    raise ForkError(f"{mismatch}\n{tail}")
                raise ForkError(f"setup step failed: {' '.join(cmd)}\n{tail}")
        if (self.root / "objdiff.json").exists() and self._all_source():
            log("compiling every source file, so objdiff can measure each one (some work in progress may not compile)")
            self.build_all(log=log)
        if not (self.root / "objdiff.json").exists():
            self.generate_objdiff(version=version, log=log)
        if not (self.root / "objdiff.json").exists():
            raise ForkError("the project built, but has no objdiff.json, so decompdecomp can't measure matches. "
                            "Some projects generate it with an extra step; check the README.")

    def _drop_stale_splat_cache(self) -> None:
        """splat skips what its cache (.splache) says it already split, and then writes the symbol lists it collects
        while splitting (undefined_syms_auto.txt) empty: a project set up before (a failed first try) would link
        without them. The cache goes when the project has such a list; the next split is a few seconds."""
        cache = self.root / ".splache"
        yaml = self._splat_yaml()
        if not cache.exists() or yaml is None:
            return
        try:
            text = yaml.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        for name in re.findall(r"^\s*undefined_(?:syms|funcs)_auto_path:\s*[\"']?([\w./-]+)", text, re.M):
            if self._inside(name) is not None:
                cache.unlink(missing_ok=True)
                return

    def generate_objdiff(self, version: str | None = None, log=print) -> bool:
        """Try the console's supported reference generator without changing an existing configuration."""
        if (self.root / "objdiff.json").exists():
            return False
        path = self._splat_yaml()
        if not version and path is not None:
            path = self._splat_yaml_of_placed_rom() or path
        if version:
            path = self._splat_version_configs().get(version) or next(
                (p for p in [*self.root.glob("*.yaml"), *self.root.glob(f"versions/{version}/*.yaml"),
                         *self.root.glob(f"config/{version}/*.yaml"), *self.root.glob(f"ver/{version}/splat.yaml")]
                 if p.name.endswith(f".{version}.yaml") or p.parent.name == version), path)
        if path is None:
            if (getattr(self, "platform", None) or self.detect_platform()) == "n64":
                # a decomp with its own Makefile and linker script and no splat config: its linker map plans the units
                from .maprefs import generate

                return generate(self, version, log=log)
            return False
        import yaml
        from elftools.common.exceptions import ELFError

        from .. import consoles

        try:
            config = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(config, dict) or not isinstance(config.get("options"), dict):
                raise ForkError("automatic objdiff setup needs a supported splat configuration")
            console = consoles.get(config["options"].get("platform"))
            segments = config.get('segments')
            if (isinstance(segments, list) and not any(
                    isinstance(segment, dict) and segment.get('type') == 'code' for segment in segments)
                    and any(self.root.glob('*/Makefile'))):
                from .splatnested import generate

                return generate(self, path, config, console, version=version, log=log)
            try:
                return console.generate_objdiff(self, path, config, log=log)
            except ForkError:
                # a splat config the independent planner can't use (compressed cartridge segments with no numeric
                # start or vram) of an N64 project whose own build matches: its linker map plans the units, each
                # proved against the original cartridge (maprefs.py, which skips any other console's project)
                from .maprefs import generate as from_map

                try:
                    done = from_map(self, version, log=log)
                except ForkError:
                    done = False
                if not done:
                    raise
                return True
        except (OSError, ValueError, ELFError, yaml.YAMLError, subprocess.TimeoutExpired, toolchains.ToolchainError) as error:
            raise ForkError(f"automatic objdiff setup could not finish: {error}. Check the project's setup manually.") from None

    def build_problem(self) -> str | None:
        """Why the project's build can't run on this computer as it is, or None: no objdiff.json, or a program the
        generated build.ninja runs by absolute path (its interpreter, a tool) that isn't there. A project folder
        copied from another home kept /home/olduser/.local/share/uv/tools/decompnet/bin/python in its build.ninja,
        and every compile failed with "/bin/sh: 1: ...: not found" until its setup ran again."""
        if not (self.root / "objdiff.json").exists():
            return "the project has no objdiff.json (its setup didn't finish)"
        ninja = self.root / "build.ninja"
        if not ninja.is_file():
            return None
        missing: list[str] = []
        try:
            with open(ninja, errors="replace", encoding="utf-8") as f:
                for line in f:
                    m = _NINJA_PROGRAM.match(line)
                    if m is None:
                        continue
                    first = m.group(2).strip().strip("'\"").split()[0] if m.group(2).strip() else ""
                    prog = first.strip("'\"")
                    # (a folder path, /home/...: not an MSVC flag like /O2 or /DNDEBUG)
                    if re.match(r"^/[^/\s]+/", prog) and prog not in missing and not Path(prog).exists():
                        missing.append(prog)
        except OSError as e:
            return f"build.ninja can't be read: {e}"
        if missing:
            return (f"build.ninja runs {', '.join(missing[:3])}, which {'is' if len(missing) == 1 else 'are'} not on "
                    "this computer (the project was set up elsewhere, or decompdecomp moved)")
        return None

    def reconfigure(self, version: str | None = None, log=print) -> tuple[bool, str]:
        """Write the build files again for this computer: the setup's first step (configure.py, with this
        computer's tool paths), not the whole setup's builds."""
        try:
            cmds = self.setup_commands(version)
        except ForkError as e:
            return False, str(e)
        if self.kind() not in ("manifest", "dtk", "configure") or not cmds:
            return False, "this kind of project's setup can't be run again by itself"
        return self.run(cmds[0], log=log, timeout=600)

    # ------------------------------------------------------------------ objdiff project

    def _config_entry(self) -> tuple[tuple, dict, dict]:
        """(stat key, objdiff.json, units by name), parsed once per change of the file: a big game's runs to
        megabytes and every compile needs it."""
        p = self.root / "objdiff.json"
        try:
            st = p.stat()
        except OSError:
            raise ForkError("objdiff.json not found: set the project up first") from None
        key = (st.st_mtime_ns, st.st_size)
        with _CONFIG_LOCK:
            hit = _CONFIG_CACHE.get(str(p))
            if hit is None or hit[0] != key:
                for wait in range(40):  # configure.py rewrites it when a file is linked: read it whole
                    try:
                        cfg = json.loads(p.read_text(encoding="utf-8"))
                        from .ninja_candidates import correct_generated_make
                        cfg = correct_generated_make(self.root, cfg)
                        break
                    except (ValueError, OSError):
                        if wait == 39:
                            if hit is not None:
                                return hit  # the last whole one: units rarely change between two writes
                            raise ForkError("objdiff.json can't be read (it's being rewritten): try again") from None
                        time.sleep(0.15)
                st = p.stat()
                key = (st.st_mtime_ns, st.st_size)
                by_name: dict[str, Unit] = {}
                for u in self._units_of(cfg):
                    by_name.setdefault(u.name, u)
                hit = _CONFIG_CACHE[str(p)] = (key, cfg, by_name)
            return hit

    def objdiff_config(self) -> dict:
        return self._config_entry()[1]

    def _units_of(self, cfg: dict) -> list[Unit]:
        tdir, bdir = cfg.get("target_dir"), cfg.get("base_dir")
        out = []
        for u in cfg.get("units") or cfg.get("objects") or []:
            meta = u.get("metadata") or {}
            t = u.get("target_path") or (str(Path(tdir) / u["path"]) if tdir and u.get("path") else None)
            b = u.get("base_path") or (str(Path(bdir) / u["path"]) if bdir and u.get("path") else None)
            out.append(Unit(u.get("name") or u.get("path") or "?", self.root / t if t else None,
                            self.root / b if b else None, meta.get("source_path"),
                            bool(meta.get("complete", u.get("complete", False))), u.get("scratch"),
                            u.get("options") or None, u.get("symbol_mappings") or None, bool(meta.get("auto_generated")),
                            bool(meta.get("autopilot_scaffold")), meta.get("autopilot_build_dir")))
        return out

    def units(self) -> list[Unit]:
        return self._units_of(self.objdiff_config())

    def unit(self, name: str) -> Unit:
        found = self._config_entry()[2].get(name)
        if found is None:
            raise ForkError(f"no unit {name!r}")
        return found

    def units_for(self, rel: str) -> list[Unit]:
        """The units whose source file is `rel` (relative to the project root)."""
        want = Path(rel).as_posix()
        return [u for u in self.units() if u.source and Path(u.source).as_posix() == want]

    def diff_options(self, unit: Unit) -> dict:
        """What every diff of this unit is judged by: the progress report's rules with the project's and unit's options."""
        return objdiff.judge_options(self.objdiff_config().get("options"), unit.options)

    def build_unit(self, unit: Unit, log=lambda *_: None) -> tuple[bool, str]:
        """Build one unit's object with the project's own build, as objdiff.json says."""
        if manifest := self.objdiff_config().get('rom_reference_manifest'):
            from .rom_judge import unit_diff
            try:
                unit_diff(self, unit, None, manifest)
                return True, ''
            except (ForkError, objdiff.ObjdiffError) as error:
                return False, str(error)
        if unit.base is None:
            return False, "this unit has no source object to build"
        cfg = self.objdiff_config()
        make = cfg.get("custom_make") or "make"
        tool = _tool(make) or make
        rel = self._target(unit.base)  # (named from the build's folder: `-C sub` builds final.o for sub/final.o)
        args = cfg.get("custom_args", [])
        if unit.scaffold and unit.scaffold_dir:
            if not isinstance(unit.scaffold_dir, str):
                return False, 'The nested candidate build directory is invalid.'
            child = Path(unit.scaffold_dir)
            root = self.root.resolve()
            folder = (self.root / child).resolve()
            if (child.is_absolute() or '..' in child.parts or root not in folder.parents
                    or folder not in unit.base.resolve().parents or not unit.source
                    or folder not in (self.root / unit.source).resolve().parents
                    or Path(tool).stem.lower() not in ('make', 'gmake')
                    or not (folder / 'Makefile').is_file()
                    or any(a.startswith(('-C', '--directory', '-f', '--file', '--makefile')) for a in args)):
                return False, 'The nested candidate build is not inside its project or has no supported Makefile.'
            target = unit.base.resolve().relative_to(folder).as_posix()
            return self.run([tool, '-C', child.as_posix(), *args, target], log=log, timeout=UNIT_SECONDS)
        if unit.scaffold and Path(tool).stem.lower() == "ninja":
            from .ninja_candidates import build
            result = build(self, unit, tool, args, log, UNIT_SECONDS)
            if result is not None and (result[0] or 'multiple rules generate' not in result[1]):
                return result
            if result is None:
                # Ninja calls an existing file with no rule "no work", even when its C changed.
                # Accept the normal graph only when it really has a build rule for this target.
                ok, query = self.run([tool, *args, '-t', 'query', rel], log=log, timeout=UNIT_SECONDS)
                if not ok or not re.search(r'^\s+input:\s*(?!phony(?:\s|$))\S+', query, re.M):
                    return False, 'This Ninja project has no usable C build rule for the candidate.'
            # A project may later add the candidate to its own graph.
        return self.run([tool, *args, rel], log=log, timeout=UNIT_SECONDS)

    def build_units(self, units: list[Unit], log=lambda *_: None) -> list[tuple[bool, str]]:
        """Build several units' objects in one run of the build: (built, the log) for each, in the order given.
        Builds take turns (they share ninja's log), and a run's own cost (reading the build file, configure's facts)
        is half a unit's build: 8 units took 5.6 s one by one, 1.3 s in one run (an x86 game's LTCG units). With
        ninja only (`-k 0`: one unit that doesn't build doesn't stop the others); other builds, and one unit, build
        alone as before. Each unit gets the verdict building it alone gives: when the run fails, a unit ninja says is
        built (a dry run, `-n`) built, and every other is built alone, so its verdict and log are its own build's
        (with buildhelp's hints). What the run printed isn't read for whose failure it was: a step's own output can
        say `FAILED: <another unit's object>` and blamed that unit. A unit that built gets no log (no caller reads a
        build's log when it built). A unit whose own build raises gets the exception in its place in the list, for
        its caller alone (_UnitBuilds)."""
        cfg = self.objdiff_config()
        if cfg.get('rom_reference_manifest'):
            return [self.build_unit(unit, log=log) for unit in units]
        make = cfg.get("custom_make") or "make"
        tool = _tool(make) or make
        if len(units) < 2 or any(u.base is None or u.scaffold for u in units) or Path(tool).stem.lower() != "ninja":
            return [self.build_unit(u, log=log) for u in units]

        def alone(u: Unit):
            try:
                return self.build_unit(u, log=log)
            except Exception as e:  # noqa: BLE001 - its caller's, not the others'
                return e

        rels = [os.path.normpath(os.path.relpath(u.base, self.root)) for u in units]
        ninja = [tool, *cfg.get("custom_args", [])]
        code, _, _ = self._execute([*ninja, "-k", "0", *dict.fromkeys(rels)], log, UNIT_SECONDS, None)
        if code == 0:
            return [(True, "")] * len(units)
        if code is None:  # out of time: which unit hung isn't known, and each alone has the whole limit
            return [alone(u) for u in units]
        done: dict[str, bool] = {}
        for r in rels:
            if r not in done:
                try:
                    done[r] = self._no_work(ninja, [r])
                except (ForkError, OSError):
                    done[r] = False
        return [(True, "") if done[r] else alone(u) for u, r in zip(units, rels)]

    def _no_work(self, ninja: list[str], targets: list[str]) -> bool:
        """Whether ninja has nothing left to build for these targets, by its own word (a dry run): a progress line
        that's missing says nothing (a project's --quiet), "no work to do" does."""
        code, said, _ = self._execute([*ninja, "-n", *targets], lambda *_: None, 120, None)
        return code == 0 and any(line.strip() == _NO_WORK for line in said)

    def failed_units(self, targets: list[str]) -> set[str]:
        """Units whose source object the build couldn't make: it failed, or a step it's made from did. A unit built
        in steps (objects linked into one, a generated source) reports only the step: `ninja -k 0` never tries what
        depends on it. Counting the object alone, an x86 game's edits that broke such a step were kept as breaking
        nothing, and the next agent to compile there was told the file was broken before it came."""
        # (the build's targets are named from the folder it runs in: `-C sub` says final.o for sub/final.o)
        if self.objdiff_config().get('rom_reference_manifest'):
            want = {os.path.normpath(t) for t in targets}
        else:
            want = {os.path.normpath(t) for t in [*targets, *self._made_from(targets)]}
        return {u.name for u in self.units() if u.base is not None and self._target(u.base) in want}

    def _build_dir(self) -> str:
        """The folder the build runs in, relative to the root: the project's `-C DIR` (ninja's and make's), else ".".
        Its targets are named from there: `ninja -C sub` builds `final.o` for sub/final.o."""
        where, found = _ninja_where(self.objdiff_config().get("custom_args", [])), "."
        for k, a in enumerate(where):
            if a == "-C" and k + 1 < len(where):
                found = os.path.join(found, where[k + 1])
            elif a.startswith("-C") and len(a) > 2:
                found = os.path.join(found, a[2:])
        return os.path.normpath(found)

    def _target(self, path) -> str:
        """A file of the project (absolute, or relative to the root) as the build names it (_build_dir)."""
        return os.path.normpath(os.path.relpath(self.root / path, self.root / self._build_dir()))

    def never_defined(self, names: set[str]) -> set[str]:
        """Which of these macros no build of the project can define: not the compiler's own (a name that starts with
        "_" or has a lowercase letter, or one compilers are known to predefine), not #defined in any of its sources,
        and named in none of its build files (a -D, a configure script). What csource.hidden_by goes by."""
        left = {n for n in names if project_macro(n)}
        if not left:
            return set()
        # (after -D or /D too: `/DNAME` has no word boundary before the name)
        words = re.compile(r"(?:\b|(?<=[-/]D))(" + "|".join(map(re.escape, sorted(left))) + r")\b")
        defines = re.compile(r"^[ \t]*#[ \t]*define[ \t]+(" + "|".join(map(re.escape, sorted(left))) + r")\b", re.M)
        for d, dirs, files in os.walk(self.root):
            # (and the build's own folders: a header the build generates there, `build/config.h`, can define one,
            # which sources include; only their headers, among the objects)
            made = Path(d) != self.root and Path(d).relative_to(self.root).parts[0] in GENERATED_DIRS
            dirs[:] = [x for x in dirs if x not in NOT_SEARCHED or Path(d) == self.root and x in GENERATED_DIRS]
            for f in files:
                p = Path(d) / f
                source = p.suffix.lower() in SOURCE_SUFFIXES
                if made and p.suffix.lower() not in HEADER_SUFFIXES:
                    continue
                if not source and p.suffix.lower() not in BUILD_SUFFIXES and f not in BUILD_FILES:
                    continue
                try:
                    if p.stat().st_size > SEARCH_MAX:
                        continue
                    text = p.read_text(errors="replace", encoding="utf-8")
                except OSError:
                    continue
                left -= set((defines if source else words).findall(text))
                if not left:
                    return set()
        return left

    def _made_from(self, targets: list[str]) -> set[str]:
        """Everything the build makes from these targets, all the way up (ninja's `-t query`, on the build file and
        folder the project's `custom_args` give ninja). A make-built project has no such query: nothing, so a step's
        failure there counts against its own target alone, as before (ROADMAP.md)."""
        cfg = self.objdiff_config()
        make = cfg.get("custom_make") or "make"
        if not targets or not (make == "ninja" or Path(make).name.startswith("ninja")):
            return set()
        tool = [_tool(make) or make, *_ninja_where(cfg.get("custom_args", []))]
        found: set[str] = set()
        todo = list(dict.fromkeys(targets))
        while todo and len(found) < MADE_FROM_MAX:
            batch, todo = todo[:QUERY_BATCH], todo[QUERY_BATCH:]
            try:
                out = subprocess.run([*tool, "-t", "query", *batch], cwd=self.root, capture_output=True, text=True,
                                     errors="replace", encoding="utf-8", timeout=60, creationflags=NO_WINDOW).stdout
            except (OSError, subprocess.TimeoutExpired):
                break
            for t in _query_outputs(out) - found:
                found.add(t)
                todo.append(t)
        return found

    def _all_source(self) -> bool:
        """dtk-template's `all_source`: every unit's source object, including units the game's link doesn't use yet
        (the default build only compiles what the link needs, so objdiff would have nothing to measure them by)."""
        try:
            with open(self.root / "build.ninja", errors="replace", encoding="utf-8") as f:
                return any(line.startswith("build all_source:") for line in f)
        except OSError:
            return False

    def build_all(self, log=lambda *_: None, failed: list[str] | None = None) -> tuple[bool, str]:
        """Build every object, going on past sources that don't compile yet (work in progress); `failed`
        collects the ones that didn't."""
        cfg = self.objdiff_config()
        if cfg.get('rom_reference_manifest'):
            problems = []
            done = 0
            check_cancel = getattr(log, 'check_cancel', lambda: None)
            for unit in self.units():
                if not unit.source:
                    continue
                check_cancel()
                ok, message = self.build_unit(unit, log=log)
                if not ok:
                    problems.append(f'{unit.name}: {message}')
                    if failed is not None and unit.base is not None:
                        failed.append(self._target(unit.base))
                done += 1
                if done % 50 == 0:
                    log(f'Built {done} ROM reference candidates')
            return not problems, '\n'.join(problems)
        make = cfg.get("custom_make") or "make"
        if (make == "ninja" or Path(make).name.startswith("ninja")) and self._all_source():
            # -k 0: keep going past work in progress that doesn't compile yet
            return self.run([_tool(make) or make, *cfg.get("custom_args", []), "-k", "0", "all_source"], log=log,
                            failed=failed)
        keep_going = ["-k"] if Path(make).name in ("make", "gmake", "mingw32-make") else []
        return self.run([_tool(make) or make, *cfg.get("custom_args", []), *keep_going], log=log, failed=failed)

    def report(self, log=print) -> dict:
        if manifest := self.objdiff_config().get('rom_reference_manifest'):
            from .rom_judge import report
            return report(self, manifest, log=log)
        return objdiff.report(self.root, self.root / STATE / "report.json")

    def diff(self, unit: Unit, symbol: str) -> objdiff.FunctionDiff:
        manifest = self.objdiff_config().get('rom_reference_manifest')
        if manifest:
            from .rom_judge import unit_diff
            return unit_diff(self, unit, symbol, manifest)
        from .linkedimages import stale
        entry = next((u for u in self.objdiff_config().get("units", []) if u.get("name") == unit.name), {})
        base = None if stale(self.root, entry, {}) else unit.base
        result = objdiff.diff(unit.target, base, symbol, cwd=self.root, options=self.diff_options(unit),
                              project=self.root if unit.mappings else None, unit=unit.name)
        if unit.source:
            source = (self.root / unit.source).resolve()
            if self.root.resolve() in source.parents and source.suffix.lower() not in ('.s', '.asm'):
                try:
                    return objdiff.exclude_diff_stubs(result, source.read_text(encoding='utf-8', errors='replace'))
                except OSError:
                    pass
            # An object left by an earlier build is not evidence of recovered C
            # when its source cannot be checked for assembly placeholders.
            result.unit_scores = dict.fromkeys(result.unit_scores)
            result.match_percent = None
        return result

    # ------------------------------------------------------------------ linking (dtk-template projects)
    # A file whose code and data match is linked into the game: configure.py marks it Matching, the build then
    # uses its compiled object instead of the original's, and the project's checksum check (`ninja .../ok`) proves
    # the whole game still comes out byte for byte. That's what decomp.dev counts as linked.

    def check_target(self) -> str | None:
        """dtk-template's checksum target (build/<version>/ok: the built DOL and RELs against the game's SHA-1s)."""
        try:
            with open(self.root / "build.ninja", errors="replace", encoding="utf-8") as f:
                for line in f:
                    m = _CHECK.match(line)
                    if m:
                        return m.group(1)
        except OSError:
            pass
        return None

    def link_states(self) -> dict[str, tuple[str, int, int]]:
        """Source file (relative to the project root) -> (its link state in configure.py: Matching, NonMatching,
        Equivalent or MatchingFor(...), where that word starts and ends in configure.py)."""
        try:
            return self._states_in((self.root / "configure.py").read_text(errors="replace", encoding="utf-8"))
        except OSError:
            return {}

    def src_dir(self) -> str:
        """Where configure.py's source paths start (dtk-template: config.src_dir, "src")."""
        try:
            m = _SRC_DIR.search((self.root / "configure.py").read_text(errors="replace", encoding="utf-8"))
        except OSError:
            m = None
        return (m.group(1) if m else "src").strip("/")

    @staticmethod
    def _states_in(text: str) -> dict[str, tuple[str, int, int]]:
        src_dir = _SRC_DIR.search(text)
        prefix = (src_dir.group(1) if src_dir else "src").strip("/")
        return {f"{prefix}/{m.group(2)}": (m.group(1), m.start(1), m.end(1)) for m in _OBJECT.finditer(text)}

    def linkable(self) -> bool:
        return self.check_target() is not None and bool(self.link_states())

    def marked_matching(self, text: str, sources: list[str], version: str | None) -> str:
        """configure.py (`text`) with these source files (NonMatching in it) marked as linked."""
        states = self._states_in(text)
        # a project that builds several versions links per version where it can say so
        word = (f'MatchingFor("{version}")' if version and len(self.versions()) > 1 and "def MatchingFor" in text
                else "Matching")
        spans = sorted((states[s][1], states[s][2]) for s in sources if s in states and states[s][0] == "NonMatching")
        for start, end in reversed(spans):
            text = text[:start] + word + text[end:]
        return text

    def image_verification(self) -> tuple[Path, str, list[list[str]]] | None:
        """An explicit whole-image check also protects partially decompiled source units."""
        manifest = self.manifest or {}
        check = manifest.get("verify_image")
        if check is None:
            return None
        game = manifest.get("game") or {}
        if not isinstance(check, dict):
            raise ForkError("verify_image must name an output and build commands")
        output, commands, sha1 = check.get("path"), check.get("commands"), game.get("sha1", "")
        if (not isinstance(output, str) or not output or not isinstance(sha1, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", sha1)
                or not isinstance(commands, list) or not commands
                or any(not isinstance(cmd, list) or not cmd
                       or any(not isinstance(arg, str) or not arg for arg in cmd) for cmd in commands)):
            raise ForkError("verify_image needs an output path, nonempty command lists and the original game's SHA-1")
        path = (self.root / output).resolve()
        if self.root.resolve() not in path.parents or path == (self.root / game.get("path", "")).resolve():
            raise ForkError("verify_image must check a rebuilt image inside the project, separate from the original")
        return path, sha1.lower(), commands

    def source_is_linked(self, unit: Unit) -> bool:
        """A source replacement must preserve an explicitly verified image, even before its unit is complete."""
        return self.image_verification() is not None or (unit.complete and self.linkable())

    def verify(self, log=lambda *_: None) -> tuple[bool, str]:
        """Build the game and check it against its checksums (regenerating the build when configure.py changed)."""
        if check := self.image_verification():
            path, expected, commands = check
            logs = []
            for command in commands:
                cmd = [sys.executable if arg in ("python", "python3") else arg for arg in command]
                ok, text = self.run(cmd, log=log, timeout=1800)
                logs.append(text)
                if not ok:
                    return False, "\n".join(logs)
            try:
                with path.open("rb") as stream:
                    actual = hashlib.file_digest(stream, "sha1").hexdigest()
            except OSError as error:
                return False, f"couldn't read rebuilt image {path.relative_to(self.root)}: {error}"
            if actual != expected:
                return False, f"rebuilt image SHA-1 {actual} does not match original SHA-1 {expected}"
            return True, "\n".join(logs + [f"rebuilt image SHA-1 matches {expected}"])
        target = self.check_target()
        if target is None:
            return False, "this project has no checksum check to run"
        cfg = self.objdiff_config()
        make = cfg.get("custom_make") or "ninja"
        return self.run([_tool(make) or make, *cfg.get("custom_args", []), target], log=log, timeout=1800)

    # ------------------------------------------------------------------ files agents may touch

    def safe_path(self, rel: str, write: bool = False) -> Path:
        """A path inside the project an agent may read, or write (`write`: only C and C++ sources and headers,
        never the build setup, targets, assembly or tools)."""
        p = (self.root / rel).resolve()
        if self.root.resolve() not in p.parents:
            raise ForkError("that path is outside the project")
        parts = set(Path(os.path.relpath(p, self.root.resolve())).parts)
        if write:
            if parts & FORBIDDEN_PARTS or not EDITABLE.search(p.name):
                raise ForkError("agents may only edit C/C++ source and header files, not the project's build setup, "
                                "targets, assembly or tools")
        elif ".git" in parts:
            raise ForkError("that path isn't readable")
        return p

    def git(self, *args: str) -> tuple[bool, str]:
        if not (self.root / ".git").exists() or not shutil.which("git"):
            return False, "not a git checkout"
        res = subprocess.run(["git", *args], cwd=self.root, capture_output=True, text=True, env=_git_env(self.root),
                             encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
        return res.returncode == 0, (res.stdout + res.stderr).strip()
