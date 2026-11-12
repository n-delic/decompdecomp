"""Real Visual C++ copies of the user's own (kits/visualcpp.py, #278), run for real: the regular test run uses
stand-ins (an empty cl.exe, a c2.dll that is only a version resource); this runs the compilers themselves, through
wibo (2014 and older) or wine (2015 and newer) as a project's build does, on Linux and macOS. Skipped unless
$DECOMPNET_REAL_VC names the copies to try, as Setup would be given them (each folder its compiler is in: VC,
VC/Tools/MSVC/<version>, or the Visual Studio folder above it), separated by os.pathsep:

    DECOMPNET_REAL_VC=/copies/msvc900/VC:/copies/vs2017/VC uv run pytest -v -s tests/test_real_visualcpp.py

For each copy and each compiler it gives (its 32-bit one, and its 64-bit one when it has it):
- the kit finds it (a copy with a 64-bit cross compiler must give it: x86_amd64, Hostx86/x64);
- a project's own tools (configure.py, ninja, compile.py, names.py) build a sample C++ file with it, and the object is
  for that compiler's processor (AMD64 or i386);
- the import picks it for a program built with it: a program whose Rich header names the object's own compiler build
  (its @comp.id), and, where wine is here and the copy has its linker, a program its own linker really linked.
Nothing touches the installed app: its toolchains ($DECOMPNET_TOOLCHAINS: wibo is downloaded into it) and wine prefix
($DECOMPNET_WINE_PREFIX, made once) are a temporary folder's, unless $DECOMPNET_REAL_VC_HOME names one to keep between
runs."""

import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from decompnet import consoles, kits, toolchains, wine
from decompnet.consoles.pc import scratch

COPIES = [Path(p) for p in os.environ.get("DECOMPNET_REAL_VC", "").split(os.pathsep) if p]
pytestmark = pytest.mark.skipif(not COPIES or sys.platform == "win32",
                                reason="set $DECOMPNET_REAL_VC to your Visual C++ copies (Linux, macOS)")

SAMPLE = """// a class, a loop, a switch and a call: code each compiler generates in its own way
struct Counter {
    int total;
    int add(int k) { total += k * 3; return total; }
};

static int pick(int i)
{
    switch (i & 3) {
    case 0: return i + 7;
    case 1: return i ^ 0x55;
    case 2: return i * i;
    default: return -i;
    }
}

extern "C" int __cdecl sample_entry(void)
{
    Counter c;
    c.total = 0;
    int t = 0;
    for (volatile int i = 0; i < 40; i++)
        t += c.add(pick(i));
    return t;
}
"""
MACHINE = {"x86": 0x14C, "x64": 0x8664}  # a COFF object's (and a PE's) machine
TIMEOUT = 600


@pytest.fixture(scope="module")
def home():
    """The toolchains and the wine prefix the run uses: never the app's."""
    keep = os.environ.get("DECOMPNET_REAL_VC_HOME")
    where = Path(keep) if keep else Path(tempfile.mkdtemp(prefix="real-vc-"))
    where.mkdir(parents=True, exist_ok=True)
    saved = {k: os.environ.get(k) for k in ("DECOMPNET_TOOLCHAINS", "DECOMPNET_WINE_PREFIX")}
    os.environ["DECOMPNET_TOOLCHAINS"] = str(where / "toolchains")
    os.environ["DECOMPNET_WINE_PREFIX"] = str(where / "wine")
    for copy in COPIES:
        toolchains.install("visualcpp", folder=copy, accept_license=True, log=print)
    toolchains.ensure("wibo", log=print)
    yield where
    if wine.find():
        wine.stop(where / "wine")
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    if not keep:
        shutil.rmtree(where, ignore_errors=True)


def _compilers() -> list[tuple[Path, kits.OwnCompiler]]:
    kit = kits.get("visualcpp")
    out = []
    for given in COPIES:
        copy = kit.locate(given)
        out += [(given, c) for c in (kit.compilers(copy) if copy else [])]
    return out


def _target(c: kits.OwnCompiler) -> str:
    return "x64" if c.version.endswith("-x64") else "x86"


def comp_id(obj: bytes) -> tuple[int, int]:
    """A COFF object's @comp.id symbol: the compiler that made it, as the Rich header names it (product, build)."""
    table, count = struct.unpack_from("<II", obj, 8)
    for i in range(count):
        at = table + 18 * i
        if obj[at:at + 8] == b"@comp.id":
            value = struct.unpack_from("<I", obj, at + 8)[0]
            return value >> 16, value & 0xFFFF
    raise AssertionError("the object has no @comp.id symbol")


def program(machine: int, product: int, build: int) -> bytes:
    """A program's headers (as tests/test_pc.py's _windows_program makes them) whose Rich header names one compiler
    build: what a game built with that compiler says about itself."""
    key = bytes([0x5A, 0x17, 0x3C, 0x91])
    head = bytearray(b"MZ" + bytes(0x3E + 0x40))
    plain = b"DanS" + bytes(12) + struct.pack("<II", product << 16 | build, 300)
    head += bytes(byte ^ key[i % 4] for i, byte in enumerate(plain)) + b"Rich" + key
    head += bytes(-len(head) % 8)
    struct.pack_into("<I", head, 0x3C, len(head))
    opt = bytearray(0xE0 if machine == 0x14C else 0xF0)
    struct.pack_into("<HBB", opt, 0, 0x10B if machine == 0x14C else 0x20B, 9, 0)
    head += b"PE\0\0" + struct.pack("<HHIIIHH", machine, 1, 0, 0, 0, len(opt), 0x102) + opt
    return bytes(head + b".text".ljust(8, b"\0") + bytes(32))


def expected(c: kits.OwnCompiler) -> str:
    """The compiler the import should pick for a game built with this copy's build: the packs' very build when they
    have it (a copy is for builds they don't), else this copy's."""
    version, number = scratch.rich_version(c.build)
    pack = (scratch.RICH_BUILDS_X64 if _target(c) == "x64" else scratch.RICH_BUILDS).get((version, number))
    return pack or c.version


def build_sample(where: Path, c: kits.OwnCompiler) -> Path:
    """The sample built by a project's own tools with the compiler, as the app builds a project's file."""
    root = where / "project"
    shutil.copytree(Path(scratch.__file__).with_name("project_template") / "tools", root / "tools")
    (root / "src" / "auto").mkdir(parents=True)
    (root / "src" / "auto" / "sample.cpp").write_text(SAMPLE, encoding="utf-8")
    (root / "include").mkdir()
    config = root / "config" / "TEST" / "functions.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"exe": "orig/test.exe", "units": {"sample": []},
                                  **({"machine": "x64"} if _target(c) == "x64" else {})}), encoding="utf-8")
    cfg = root / "tools" / "configure.py"
    pe = scratch.read_pe(program(MACHINE[_target(c)], 0, 0))
    cfg.write_text(scratch.configure_settings(cfg.read_text(encoding="utf-8"), c.version, pe, log=print,
                                              ehsc=scratch._version_key(scratch.rich_version(c.build)[0]) >= (8, 0)),
                   encoding="utf-8")
    pack = where / "no-pack"  # (configure.py wants an --msvc folder: every compiler here is the copy's)
    pack.mkdir()
    args = ["--msvc", str(pack), "--wibo", str(toolchains.binary("wibo")),
            *scratch.own_compiler_args(kits.compilers(("visualcpp",)))]
    # what the app gives a project's build (wine, #278), and nothing of this run's own: its wine prefix setting would
    # hide a build that the app gives none
    env = {**{k: v for k, v in os.environ.items() if not k.startswith("DECOMPNET_WINE")},
           **consoles.get("pc").build_env()}
    subprocess.run([sys.executable, str(cfg), "TEST", *args], cwd=root, env=env, check=True, timeout=TIMEOUT)
    ninja = shutil.which("ninja") or str(Path(sys.executable).with_name("ninja"))
    obj = root / "build" / "TEST" / "src" / "auto" / "sample.obj"
    run = subprocess.run([ninja, obj.relative_to(root).as_posix()], cwd=root, env=env, capture_output=True,
                         text=True, timeout=TIMEOUT)
    assert run.returncode == 0, f"{c.name} didn't build the sample:\n{run.stdout}\n{run.stderr}"
    return obj


def windows(path) -> str:
    """A path as a program under wine reads it (Z: is /)."""
    return "Z:" + str(path).replace("/", "\\")


def link_with_its_linker(where: Path, c: kits.OwnCompiler, obj: Path) -> bytes | None:
    """A program the copy's own linker links from the sample (no C runtime: its entry is the sample's), through wine;
    None where there's no wine or no linker beside the compiler. (wibo 1.2.0 doesn't run 2005-2010's linkers.)"""
    link = next((p for p in c.exe.parent.iterdir() if p.name.lower() == "link.exe"), None)
    found = wine.find()
    if link is None or not found or wine.problem() is not None:
        return None
    prefix = wine.prepare(log=print, where=Path(os.environ["DECOMPNET_WINE_PREFIX"]))
    out = where / "sample.exe"
    env = {**wine.environment(prefix), "WINEPATH": ";".join(windows(d) for d in (c.exe.parent, *c.dlls))}
    run = subprocess.run([found, str(link), "/nologo", "/NODEFAULTLIB", "/ENTRY:sample_entry", "/SUBSYSTEM:CONSOLE",
                          f"/MACHINE:{'X64' if _target(c) == 'x64' else 'X86'}", f"/OUT:{windows(out)}", windows(obj)],
                         env=env, capture_output=True, text=True, timeout=TIMEOUT)
    assert run.returncode == 0 and out.is_file(), f"{link} didn't link the sample:\n{run.stdout}\n{run.stderr}"
    return out.read_bytes()


@pytest.mark.parametrize("given,c", _compilers() if COPIES else [],
                         ids=lambda v: v.version if isinstance(v, kits.OwnCompiler) else Path(v).name)
def test_a_real_copys_compiler_builds_the_sample_and_the_import_picks_it(home, tmp_path, given, c):
    kit = kits.get("visualcpp")
    copy = kit.locate(given)
    print(f"\n{c.name}: {c.exe} ({c.runner}); Setup says: {kit.verify(copy)}")
    if _target(c) == "x86" and re.search(r"(?i)x86_amd64|hostx86[/\\]x64", " ".join(
            p.as_posix() for p in copy.rglob("cl.exe"))):
        # (#278: a copy with a 64-bit cross compiler must give it beside its 32-bit one)
        assert any(o.version == c.version + "-x64" for o in kit.compilers(copy)), "its 64-bit compiler wasn't found"
    obj = build_sample(tmp_path, c)
    data = obj.read_bytes()
    assert struct.unpack_from("<H", data, 0)[0] == MACHINE[_target(c)], f"{c.name} made another processor's code"
    product, build = comp_id(data)
    assert build == scratch.rich_version(c.build)[1], f"its @comp.id says build {build}, its c2.dll {c.build}"
    # the import, for a program built with this build (its Rich header naming the object's own compiler)
    built = scratch.describe(program(MACHINE[_target(c)], product, build))
    assert (built.version, built.build) == scratch.rich_version(c.build)
    assert built.pack_version == expected(c), f"the import picked {built.pack_version}, not {expected(c)}"
    assert built.own is None or built.own.version == c.version
    game = tmp_path / "game.exe"
    game.write_bytes(program(MACHINE[_target(c)], product, build))
    info = scratch.game_info(game)
    assert (info["compiler"], info["target"]) == (expected(c), _target(c))
    assert info["cannot_match"] == [] and info["exact"], info
    print(f"  built a {_target(c)} object (comp.id {product:#x}, build {build}); the import picks {info['compiler']}: "
          f"{info['compiler_why']}")
    linked = link_with_its_linker(tmp_path, c, obj)
    if linked is None:
        print("  (no real program linked: needs wine and the copy's link.exe beside its compiler)")
        return
    real = scratch.describe(linked)
    assert real.machine == scratch.MACHINES[MACHINE[_target(c)]] and real.pack_version == expected(c), real
    print(f"  its own linker's program: {real.summary()}; the import picks {real.pack_version}")
