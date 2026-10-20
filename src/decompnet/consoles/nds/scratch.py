"""Start a Nintendo DS decompilation from scratch: a ds-decomp project made for the user's own game.

ds-decomp (dsd, MIT) is the community's DS toolkit: it extracts the ROM, analyses the ARM9 program, its ITCM and DTCM
parts and every overlay, finds the functions and delinks the code into objects. The generated project (tools/
configure.py, modeled on the CC0 build of one of AetiasHax's DS decomps) compiles src/ with CodeWarrior for DS,
links it with CodeWarrior's linker, rebuilds the ROM and checks it against the user's copy, byte for byte, on every
build. So the result is the base for ports and mods from the first minute, and the same kind of project as the DS
decomps on decomp.dev.

Each module's code gets source files (src/auto/<module>_<address>.c, about 4 KiB of functions each) for agents to
write into; decompdecomp's fork engine then works on it like on any community project, judged by objdiff.

Games mix CodeWarrior versions (issue #23: functions stuck at 80-95% under 2.0 because the original's epilogue is
1.2's `ldmia {..., lr}; bx lr`), so each source file can have its own: config/<version>/arm9/file_compilers.json, the
layout DS decomps use (khdays-decomp), which tools/configure.py reads.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

from ... import toolchains
from ...forks.project import ForkError, ForkProject

DS_DEFAULT = "2.0/sp1p5"
UNIT_BYTES = 0x1000
TEMPLATE = Path(__file__).with_name("project_template")
REGIONS = {"E": "North America", "P": "Europe", "J": "Japan", "K": "Korea", "D": "Germany", "F": "France",
           "I": "Italy", "S": "Spain", "U": "Australia", "C": "China"}
_FUNC = re.compile(r"^(\S+) kind:function\((arm|thumb),size=(0x[0-9a-fA-F]+)\)[^\n]*?addr:(0x[0-9a-fA-F]+)", re.M)
_SECTION = re.compile(r"^\s+(\.\w+)\s+start:(0x[0-9a-fA-F]+)\s+end:(0x[0-9a-fA-F]+)", re.M)


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def is_nds(head: bytes) -> bool:
    """A DS ROM: the header's own checksum is right and the ARM9 program sits where DS ROMs put it."""
    if len(head) < 0x200:
        return False
    if crc16(head[:0x15E]) != struct.unpack_from("<H", head, 0x15E)[0]:
        return False
    arm9_off, entry, ram, size = struct.unpack_from("<IIII", head, 0x20)
    return arm9_off >= 0x200 and 0x02000000 <= ram < 0x02400000 and ram <= entry < ram + size


def game_info(path: Path) -> dict:
    with open(path, "rb") as f:
        head = f.read(0x200)
    if not is_nds(head):
        raise ForkError("this isn't a Nintendo DS ROM (.nds)")
    code = head[12:16].decode("ascii", "replace")
    title = head[:12].split(b"\0", 1)[0].decode("ascii", "replace").strip() or code
    return {"platform": "nds", "id": code, "title": title, "region": REGIONS.get(code[3:], "Unknown region"),
            "sha1": hashlib.sha1(path.read_bytes()).hexdigest()}


def versions() -> list[str]:
    """The CodeWarrior for DS versions in the installed compiler pack ("2.0/sp1p5", "dsi/1.2p1", ...)."""
    if toolchains.installed("mwccarm") is None:
        return []
    base = Path(toolchains.path("mwccarm")) / "mwccarm"
    return sorted(p.parent.relative_to(base).as_posix() for p in base.glob("*/*/mwccarm.exe"))


def dsd_path() -> str:
    toolchains.ensure("dsd", log=lambda *_: None)
    return str(toolchains.binary("dsd"))


def tool_args() -> list[str]:
    """configure.py arguments pointing at decompdecomp's pinned tools."""
    toolchains.ensure("dsd", log=lambda *_: None)
    if toolchains.installed("mwccarm") is None:
        raise ForkError("DS games are built with CodeWarrior for DS, which is proprietary: install it in Setup "
                        "(CodeWarrior for DS) if you accept that, then try again")
    args = ["--dsd", str(toolchains.binary("dsd")), "--compilers", str(Path(toolchains.path("mwccarm")) / "mwccarm")]
    if toolchains.get("wibo").supported and sys.platform != "win32":
        toolchains.ensure("wibo", log=lambda *_: None)
        args += ["--wibo", str(toolchains.binary("wibo"))]
    return args


TYPES_H = """/* Basic types for this decompilation (CodeWarrior for DS conventions). Agents add structs and prototypes here or
 * in new headers next to it. */
#ifndef COMMON_H
#define COMMON_H

typedef signed char s8;
typedef unsigned char u8;
typedef signed short s16;
typedef unsigned short u16;
typedef signed int s32;
typedef unsigned int u32;
typedef signed long long s64;
typedef unsigned long long u64;
typedef float f32;
typedef double f64;
typedef int BOOL;
#define TRUE 1
#define FALSE 0
#ifndef NULL
#define NULL ((void*)0)
#endif

#endif
"""


def _run(cmd: list[str], cwd: Path, what: str) -> None:
    from ...limits import capped

    res = subprocess.run(capped(cmd), cwd=cwd, capture_output=True, text=True, timeout=3600,
                         encoding="utf-8", errors="replace")
    if res.returncode != 0:
        raise ForkError(f"{what} failed:\n{(res.stdout + res.stderr).strip()[-1500:]}")


def compilers_file(root: Path, version: str) -> Path:
    return root / "config" / version / "arm9" / "file_compilers.json"


def file_compilers(root: Path, version: str) -> dict[str, str]:
    """{source file: CodeWarrior version} for the files not built with the project's own version."""
    try:
        return json.loads(compilers_file(root, version).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def set_file_compiler(root: Path, version: str, source: str, compiler: str, default: str) -> None:
    """Build `source` with `compiler` ("DS/1.2/sp4"); the project's own version (`default`) needs no line."""
    found = file_compilers(root, version)
    found.pop(source, None)
    if compiler.removeprefix("DS/") != default.removeprefix("DS/"):
        found[source] = compiler.removeprefix("DS/")
    path = compilers_file(root, version)
    tmp = path.with_suffix(".tmp")  # a build reading it meanwhile sees the old map or the new one, never half
    tmp.write_text(json.dumps(dict(sorted(found.items())), indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


_DELINK_FILE = re.compile(r"^(\S+):[ \t]*$", re.M)
_TEXT_RANGE = re.compile(r"^\s+\.text\s+start:(0x[0-9a-fA-F]+)\s+end:(0x[0-9a-fA-F]+)", re.M)


def file_ranges(root: Path, version: str) -> dict[str, list[tuple[int, int]]]:
    """{source file: [(start, end)]} of its code, from the delinks.txt of every module (each file's lines there)."""
    out: dict[str, list[tuple[int, int]]] = {}
    for delinks in sorted((root / "config" / version).rglob("delinks.txt")):
        text = delinks.read_text(encoding="utf-8")
        heads = list(_DELINK_FILE.finditer(text))
        for k, m in enumerate(heads):
            body = text[m.end():heads[k + 1].start() if k + 1 < len(heads) else len(text)]
            out.setdefault(m.group(1), []).extend((int(s, 16), int(e, 16)) for s, e in _TEXT_RANGE.findall(body))
    return out


def set_own_compiler(root: Path, manifest: dict, compiler: str, sources: list[str]) -> list[str]:
    """`compiler` ("DS/1.2/sp4") as the project's own version (tools/configure.py's MWCC_VERSION and the manifest's),
    every source file keeping the version it has: the map then names the files built with another."""
    version, old, new = manifest["version"], manifest.get("compiler", f"DS/{DS_DEFAULT}"), compiler.removeprefix("DS/")
    found = file_compilers(root, version)
    has = {s: found.get(s, old.removeprefix("DS/")) for s in sources}
    path = compilers_file(root, version)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({s: v for s, v in sorted(has.items()) if v != new}, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    cfg = root / "tools" / "configure.py"
    cfg.write_text(re.sub(r'^MWCC_VERSION = "[^"]*"', f'MWCC_VERSION = "{new}"', cfg.read_text(encoding="utf-8"),
                          count=1, flags=re.M), encoding="utf-8")
    (root / ".decomp.wtf.json").write_text(json.dumps({**manifest, "compiler": compiler}, indent=2), encoding="utf-8")
    return [path.relative_to(root).as_posix(), "tools/configure.py", ".decomp.wtf.json"]


def _modules(config: Path) -> list[tuple[str, Path]]:
    """(file name prefix, module directory) for every module dsd made: main, ITCM, DTCM, overlays."""
    out = []
    for delinks in sorted(config.rglob("delinks.txt")):
        rel = delinks.parent.relative_to(config).as_posix()
        name = {"arm9": "main"}.get(rel, rel.split("/")[-1])
        m = re.match(r"ov(\d+)$", name)
        out.append((f"ov{int(m.group(1)):03d}" if m else name, delinks.parent))
    return out


def _units(module: Path, since: int | None = None) -> list[tuple[int, int, int]]:
    """(start, end, functions) of the source files for a module's .text (from `since`): runs of functions of about
    UNIT_BYTES that together cover the whole section (bytes between functions go with the file before them; a stray
    piece on its own could be dead-stripped by the linker)."""
    delinks = (module / "delinks.txt").read_text(encoding="utf-8")
    text = next(((int(s, 16), int(e, 16)) for name, s, e in _SECTION.findall(delinks.split("\n\n")[0]) if name == ".text"),
                None)
    if text is not None and since is not None:
        text = (since, text[1]) if since < text[1] else None
    if text is None:
        return []
    symbols = (module / "symbols.txt").read_text(encoding="utf-8")
    funcs = sorted({int(a, 16): int(size, 16) for _, _, size, a in _FUNC.findall(symbols)
                    if text[0] <= int(a, 16) < text[1]}.items())
    units, cur = [], None
    for addr, size in funcs:
        end = min(addr + size, text[1])
        if cur and (end - cur[0] > UNIT_BYTES or addr < cur[1]):
            units.append(tuple(cur))
            cur = None
        if cur is None:
            cur = [addr, end, 1]
        else:
            cur[1], cur[2] = end, cur[2] + 1
    if cur:
        units.append(tuple(cur))
    if not units:
        return []
    starts = [text[0]] + [u[0] for u in units[1:]]
    ends = [u[0] for u in units[1:]] + [text[1]]
    return [(a, b, u[2]) for a, b, u in zip(starts, ends, units)]


def _write_sources(prefix: str, module: Path, root: Path, units: list[tuple[int, int, int]]) -> list[str]:
    """Each unit's source file, empty but for its header, in the module's delinks.txt."""
    lines = (module / "delinks.txt").read_text(encoding="utf-8").rstrip("\n") + "\n"
    names = []
    for start, end, count in units:
        name = f"src/auto/{prefix}_{start:08X}.c"
        lines += f"\n{name}:\n    .text start:0x{start:08x} end:0x{end:08x}\n"
        src = root / name
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text(f"/* {count} function{'s' if count != 1 else ''} from 0x{start:08X} to 0x{end:08X} "
                       f"({prefix}), found by ds-decomp. */\n#include \"common.h\"\n", encoding="utf-8")
        names.append(name)
    (module / "delinks.txt").write_text(lines, encoding="utf-8")
    return names


def add_sources(root: Path, version: str) -> list[str]:
    """Source files for the code a module's .text gained past its last one (recover.extend_text). Returns them."""
    names = []
    for prefix, module in _modules(root / "config" / version):
        ends = [int(e, 16) for e in re.findall(r"^\s+\.text start:0x[0-9a-f]+ end:(0x[0-9a-f]+)",
                                               (module / "delinks.txt").read_text(encoding="utf-8").split("\n\n", 1)[-1],
                                               re.M)]
        if ends:
            names += _write_sources(prefix, module, root, _units(module, since=max(ends)))
    return names


def _init(dsd: str, root: Path, version: str, log) -> None:
    init = [dsd, "init", "--rom-config", f"extract/{version}/config.yaml", "--output-path", f"config/{version}",
            "--build-path", f"build/{version}"]
    try:
        _run(init, root, "analysing the game")
    except ForkError as e:
        # a call into code dsd's analysis didn't take for a function (a DS game calls 0x0206135C):
        # dsd's own option gives each such target a function symbol of unknown size, and the analysis goes on
        if "leads to no function" not in str(e):
            raise
        log("the game calls code ds-decomp didn't find as a function: analysing again, with each such call's "
            "target as a function of its own")
        shutil.rmtree(root / "config" / version, ignore_errors=True)
        _run([*init, "--allow-unknown-function-calls"], root, "analysing the game")


def write_tools(root: Path, game: str | None = None, compiler: str | None = None) -> list[str]:
    """The template's build tools in a project, for `game` built with `compiler` ("2.0/sp1p5"; None: what its
    configure.py has now). A project made by an older decompdecomp gets the fixes its build has had since (agents
    never edit tools/: forks/project.py). Returns the files that changed."""
    cfg = root / "tools" / "configure.py"
    now = cfg.read_text(encoding="utf-8") if cfg.exists() else ""
    keep = {key: value or (m.group(1) if (m := re.search(rf'^{key} = "([^"]*)"', now, re.M)) else None)
            for key, value in (("GAME", game), ("MWCC_VERSION", compiler))}
    changed = []
    for src in sorted((TEMPLATE / "tools").glob("*.py")):
        text = src.read_text(encoding="utf-8")
        if src.name == "configure.py":
            for key, value in keep.items():
                if value:
                    text = re.sub(rf'^{key} = "[^"]*"', f'{key} = "{value}"', text, count=1, flags=re.M)
        dest = root / "tools" / src.name
        if not dest.exists() or dest.read_text(encoding="utf-8") != text:
            dest.write_text(text, encoding="utf-8")
            changed.append(f"tools/{src.name}")
    return changed


def create(root: Path, game: Path, *, compiler: str | None = None, log=print) -> dict:
    """Make the project in `root` (which must not exist yet) and build it once. Returns facts about the game."""
    info = game_info(game)
    version = info["id"].lower()
    compiler = (compiler or f"DS/{DS_DEFAULT}").removeprefix("DS/")
    if compiler not in versions():
        raise ForkError(f"CodeWarrior for DS {compiler} isn't in the installed compiler pack")
    args = tool_args()
    shutil.copytree(TEMPLATE, root)
    (root / "extract").mkdir()
    try:
        (root / "extract" / "baserom.nds").hardlink_to(game)
    except OSError:
        shutil.copyfile(game, root / "extract" / "baserom.nds")
    (root / "include").mkdir()
    (root / "include" / "common.h").write_text(TYPES_H, encoding="utf-8")
    write_tools(root, version, compiler)

    dsd = args[args.index("--dsd") + 1]
    log(f"extracting {info['title']} ({info['id']}) with ds-decomp")
    _run([dsd, "rom", "extract", "--rom", "extract/baserom.nds", "--output-path", f"extract/{version}"], root,
         "extracting the ROM")
    log("analysing the ARM9 program, its autoloads and overlays (dsd init)")
    _init(dsd, root, version, log)
    from . import recover

    log("finding the functions dsd's analysis missed (functions only pointers reach)")
    recover.recover(root, version, dsd=dsd, log=log)

    sources = []
    for prefix, module in _modules(root / "config" / version):
        if units := _units(module):
            sources += _write_sources(prefix, module, root, units)
    log(f"{len(sources)} source files for agents to write into")
    compilers_file(root, version).write_text("{}\n", encoding="utf-8")

    fp = ForkProject(root)
    setup = [[sys.executable, "tools/configure.py", version, *args], ["ninja", "prepare"]]
    for cmd in setup:
        ok, tail = fp.run(cmd, log=log, timeout=3600)
        if not ok:
            raise ForkError(f"the first build failed:\n{tail[-1500:]}")
    if not (root / "objdiff.json").exists():
        raise ForkError("the first build finished without writing objdiff.json")
    rebuild = check_rom(root, version, log=log)

    (root / ".decomp.wtf.json").write_text(json.dumps({
        "created": "from scratch", "platform": "nds", "version": version, "compiler": f"DS/{compiler}",
        "analysis": 4,  # (consoles/nds: NintendoDS.ANALYSIS)
        "game": {"what": "your copy of the game (.nds)", "path": "extract/baserom.nds", "sha1": info["sha1"]},
        "setup": [["python", "tools/configure.py", version, *args], ["ninja", "prepare"]],
        "rebuild": rebuild,
    }, indent=2), encoding="utf-8")
    (root / ".gitignore").write_text("# made from your copy of the game, or by the build\nextract/\nbuild/\nbuild.ninja\n"
                                     ".ninja_*\nobjdiff.json\n__pycache__/\n.decomp-wtf/\n", encoding="utf-8")
    (root / "README.md").write_text(f"""# {info['title']} ({info['id']}) decompilation

Started from scratch with [decompdecomp](https://github.com/n-delic/decompdecomp) on [ds-decomp](https://github.com/AetiasHax/ds-decomp),
the toolkit DS decomps use. Every build rebuilds the ROM and checks it against your copy, byte for byte.

1. Put your own copy of the game here as `extract/baserom.nds` (never commit it).
2. `python tools/configure.py {version} --dsd <dsd> --compilers <CodeWarrior for DS> [--wibo <wibo>]`, then `ninja`.
3. Decompiled code goes in `src/`; `objdiff.json` shows each function's progress in [objdiff](https://github.com/encounter/objdiff).

Compiler: CodeWarrior for DS {compiler} (`MWCC_VERSION` in tools/configure.py). Files the game built with another
version name it in `config/{version}/arm9/file_compilers.json` (`{{"src/auto/main_02000000.c": "1.2/sp4"}}`).
`config/{version}/` holds what ds-decomp found: sections, symbols, relocations and the source files (`delinks.txt`).
""", encoding="utf-8")
    if shutil.which("git"):
        for cmd in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"],
                    ["git", "-c", "user.name=decompdecomp", "-c", "user.email=noreply@decompdecomp.invalid", "commit", "-q", "-m",
                     f"Start the {info['id']} decompilation (decompdecomp, ds-decomp)"]):
            subprocess.run(cmd, cwd=root, capture_output=True)
    return {"platform": "nds", "id": info["id"], "title": info["title"], "compiler": f"DS/{compiler}",
            "sources": len(sources), "sha1": info["sha1"], "rebuild": rebuild}


def check_rom(root: Path, version: str, log=print) -> dict:
    """Rebuild the ROM from everything delinked and compiled, and compare it with the user's copy (`ninja rom`).

    Decompiling doesn't depend on it (each function is compared with its own delinked original), so a ROM that doesn't
    come out identical yet is reported, not fatal: tools/same.py says which part differs (issue #6: a DS game's
    copy whose rebuild differed stopped the whole import)."""
    ok, tail = ForkProject(root).run(["ninja", "rom"], log=lambda *_: None, timeout=3600)
    stamp = root / "build" / version / "rom.ok"
    if ok and stamp.exists():
        note = stamp.read_text(encoding="utf-8").strip()
        detail = "identical up to the padding at the end (your copy is trimmed)" if "padding" in note else "identical"
        log(f"the rebuilt ROM is your copy, byte for byte{' up to the padding at its end' if 'padding' in note else ''}")
        return {"identical": True, "detail": detail}
    lines = [line.strip()[2:] for line in tail.splitlines() if line.strip().startswith("- ")]
    detail = "; ".join(lines) or (tail.strip().splitlines() or ["the ROM build failed"])[-1][-300:]
    log(f"note: the rebuilt ROM isn't your copy byte for byte yet ({detail}). Decompiling works anyway: every "
        "function is compared with your copy's own code.")
    return {"identical": False, "detail": detail}
