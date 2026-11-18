"""The Visual C++ flags search_flags may try on a PC or Xbox game's source file (consoles/pc/clflags.py): which
versions and targets take each, the choices they make, what an agent may name, and (where the packs are installed)
that each Visual C++ takes what the catalogue says it does."""

import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from decompnet import consoles, toolchains
from decompnet.consoles.pc import clflags
from decompnet.network import NetworkError


def flags(compiler: str, target: str = "x86") -> set[str]:
    return {f.flag for f in clflags.available(compiler, target)}


def test_each_version_takes_its_own_flags():
    six, seven, eight = flags("MSVC/6.0"), flags("MSVC/7.1"), flags("MSVC/8.0")
    assert not {"/hotpatch", "/GS-", "/GS", "/arch:SSE2", "/fp:fast", "/Zc:wchar_t"} & six
    assert {"/G5", "/G6", "/GB", "/Op", "/Oa", "/Ow", "/Gf", "/ML", "/GX", "/QIfdiv"} <= six
    assert {"/GS", "/G7", "/arch:SSE2"} <= seven and "/GS-" not in seven and "/G7" not in flags("MSVC/7.0")
    assert {"/hotpatch", "/GS-", "/Gy-", "/fp:fast", "/fp:strict", "/openmp", "/vd2"} <= eight
    assert not {"/G6", "/Op", "/Oa", "/Gf", "/ML", "/GX"} & eight  # (2005 dropped them)
    assert "/arch:AVX" in flags("MSVC/10.0") and "/arch:AVX" not in flags("MSVC/9.0SP1")
    assert "/Qfast_transcendentals" in flags("MSVC/9.0") and "/Qfast_transcendentals" not in flags("MSVC/8.0SP1")
    assert "/arch:AVX512" not in flags("MSVC/own-19.10.25017") and "/arch:AVX512" in flags("MSVC/own-19.16.27023")
    assert "/Ob3" in flags("MSVC/own-19.29.30137") and "/Ob3" not in flags("MSVC/14.16")
    assert {"/guard:cf", "/Zc:threadSafeInit-", "/Gv"} <= flags("MSVC/14.16")


def test_x86_and_x64_flags_are_apart():
    x86, x64 = flags("MSVC/14.16"), flags("MSVC/14.16-x64", "x64")
    assert {"/Oy-", "/Gz", "/Gr", "/arch:SSE2", "/QIfist"} <= x86 and not {"/Oy-", "/Gz", "/arch:SSE2", "/QIfist"} & x64
    assert {"/favor:AMD64", "/homeparams"} <= x64 and not {"/favor:AMD64", "/homeparams"} & x86
    assert {"/arch:AVX", "/hotpatch", "/GS-"} <= x64 & x86


def test_compiler_names_become_versions():
    assert clflags.version_key("MSVC/6.0") == (6, 0, 0) and clflags.version_key("10.0SP1") == (10, 0, 1)
    assert clflags.version_key("MSVC/14.16-x64") == (14, 16, 0)
    assert clflags.version_key("MSVC/own-12.00.8168") == (6, 0, 0)  # cl.exe's version: 12 is 6.0
    assert clflags.version_key("own-13.10.3077") == (7, 1, 0) and clflags.version_key("own-16.00.40219") == (10, 0, 1)
    assert clflags.version_key("own-19.29.30137-x64") == (14, 29, 0) and clflags.version_key("xdk-3911") == (7, 0, 0)


def test_named_flags_are_checked_against_the_files_compiler():
    assert clflags.parse("/GS- /hotpatch", "MSVC/8.0", "x86") == ["/GS-", "/hotpatch"]
    assert clflags.parse("-O1  -Oy-", "MSVC/6.0", "x86") == ["/O1", "/Oy-"]  # (cl.exe takes - for /)
    assert clflags.parse("/Gs4096 /Gs4096", "MSVC/6.0", "x86") == ["/Gs4096"]
    with pytest.raises(NetworkError, match=r"isn't taken by Visual C\+\+ 6.0 \(x86\)\. Allowed, by group: frame "
                                           r"pointer: /Oy- /Oy;") as said:
        clflags.parse("/GS-", "MSVC/6.0", "x86")
    assert "/hotpatch" not in str(said.value) and "/Gs<bytes>" in str(said.value)
    with pytest.raises(NetworkError, match="isn't taken"):
        clflags.parse("/Oy-", "MSVC/14.16-x64", "x64")
    with pytest.raises(NetworkError, match="one choice"):
        clflags.parse("/O1 /O2", "MSVC/7.1", "x86")
    with pytest.raises(NetworkError, match="intermediate code"):
        clflags.parse("/GL", "MSVC/7.1", "x86")
    with pytest.raises(NetworkError, match="intermediate code"):
        clflags.parse("-GL", "MSVC/7.1", "x86")


@pytest.mark.parametrize("named", [
    "/Fo../x.obj", "/Fa", "/Fdx.pdb", "/FIwindows.h", "/I..", "/Iinclude", "/DX=1", "/U_WIN32", "/B1x", "/B1",
    "/B2c2.dll", "/Bx", "/link", "@flags.rsp", "/O1@x", "\"/O1\"", "'/O1'", "/O1\";", "/O1;rm", "O1", "/gs-",
    "/Gs4096x", "/Gs-1", "/Yc", "/Yustdafx.h", "/Fpx.pch", "/Zi", "/LTCG", "/bogus", "/O1\x00", "/O1,/Od",
    "/O1 /link", "/Gs99999999", "/arch:AVX/../x",
])
def test_nothing_but_the_catalogues_flags_gets_through(named):
    with pytest.raises(NetworkError, match="Allowed, by group|intermediate code|name one or more"):
        clflags.parse(named, "MSVC/14.16", "x86")


def test_empty_or_odd_input_is_refused():
    for named in ("", "   ", None, 7):
        with pytest.raises(NetworkError):
            clflags.parse(named, "MSVC/7.1", "x86")


def test_the_sweep_changes_one_choice_at_a_time():
    sweep = clflags.variants([], "MSVC/8.0", "x86")
    assert sweep[:5] == [[], ["/Oy-"], ["/O1"], ["/Ob1"], ["/O1", "/Oy-"]]  # what matters most first
    singles = sweep[5:]
    assert all(len(v) == 1 for v in singles)  # one flag each, no combinations
    assert {"/GS-", "/hotpatch", "/Gy-", "/Oi-", "/fp:fast", "/arch:SSE2", "/Gz", "/EHa", "/Zp1"} <= {v[0] for v in singles}
    assert not {"/O2", "/Oy", "/GS", "/Gd", "/Zp8", "/fp:precise"} & {v[0] for v in singles}  # (what /O2 gives)
    assert not {"/RTC1", "/Qimprecise_fwaits"} & {v[0] for v in singles}  # (they need /Od, /fp:except)
    assert sweep.index(["/GS-"]) < sweep.index(["/hotpatch"]) < sweep.index(["/fp:fast"]) < sweep.index(["/Zp1"])
    assert ["/G6"] in clflags.variants([], "MSVC/6.0", "x86") and ["/GS-"] not in clflags.variants([], "MSVC/6.0", "x86")
    assert not any("/Oy-" in v for v in clflags.variants([], "MSVC/14.16-x64", "x64"))
    assert len(sweep) == len({tuple(v) for v in sweep})


def test_a_files_own_flag_is_taken_away_or_replaced_by_its_groups_others():
    sweep = clflags.variants(["/Oy-", "/Od"], "MSVC/8.0", "x86")
    assert sweep[0] == ["/Oy-", "/Od"] and ["/Od"] in sweep and ["/Oy-"] in sweep  # each taken away
    assert ["/Oy-", "/O1"] in sweep and ["/Oy-", "/Ox"] in sweep and ["/Oy-", "/O2"] in sweep  # /Od replaced
    assert ["/Oy-", "/Od", "/RTC1"] in sweep  # (with /Od, the run-time checks build)
    assert not any("/Od" in v and "/O1" in v for v in sweep)


def test_named_flags_are_tried_together_then_each_alone():
    out = clflags.variants(["/Oy-", "/O1"], "MSVC/8.0", "x86", ["/O2", "/GS-"])
    assert out == [["/Oy-", "/O1"], ["/Oy-", "/O2", "/GS-"], ["/Oy-", "/O2"], ["/Oy-", "/O1", "/GS-"]]
    assert clflags.variants(["/Oy-"], "MSVC/8.0", "x86", ["/hotpatch"]) == [["/Oy-"], ["/Oy-", "/hotpatch"]]
    assert clflags.variants(["/Oy-"], "MSVC/8.0", "x86", ["/Oy-"]) == [["/Oy-"]]  # (what the file has already)
    assert clflags.variants(["/Zp1", "/Gz"], "MSVC/8.0", "x86", ["/Zp4"]) == [["/Zp1", "/Gz"], ["/Gz", "/Zp4"]]


def _project(tmp_path, compiler: str) -> SimpleNamespace:
    (tmp_path / "config" / "T").mkdir(parents=True)
    (tmp_path / "config" / "T" / "file_compilers.json").write_text("{}")
    (tmp_path / "config" / "T" / "file_flags.json").write_text("{}")
    return SimpleNamespace(root=tmp_path, manifest={"version": "T", "compiler": compiler})


def test_pc_and_xbox_check_named_flags_against_the_files_compiler(tmp_path):
    pc, xbox = consoles.get("pc"), consoles.get("xbox")
    six = _project(tmp_path / "pc", "MSVC/6.0")
    assert pc.flag_variants([], six, "src/a.cpp", "/G5") == [[], ["/G5"]]
    with pytest.raises(NetworkError, match="isn't taken by Visual C\\+\\+ 6.0"):
        pc.flag_variants([], six, "src/a.cpp", "/hotpatch")
    seven = _project(tmp_path / "xbox", "MSVC/7.1")  # (an Xbox game's 7.1: it builds through the PC's project)
    assert xbox.flag_variants([], seven, "src/a.cpp", "/G7 /arch:SSE") == [[], ["/G7", "/arch:SSE"], ["/G7"],
                                                                           ["/arch:SSE"]]
    with pytest.raises(NetworkError, match="Allowed, by group"):
        xbox.flag_variants([], seven, "src/a.cpp", "/FIx.h")
    assert ["/G7"] in xbox.flag_variants([], seven, "src/a.cpp")  # (its sweep: 7.1's flags)
    assert "search_flags" in xbox.own_tools


# ---------------------------------------------------------------------------------------- what each cl.exe takes

C_FILE = "int g[4];\nint f(int i) { return g[i & 3] * 3 + i; }\n"
PACKS = {"msvc": ("6.0", "7.0", "7.1"), "msvc2005": ("8.0", "8.0SP1", "9.0", "9.0SP1", "10.0", "10.0SP1")}
INSTALLED = [(pack, v) for pack, versions in PACKS.items() if toolchains.installed(pack) for v in versions
             if (toolchains.path(pack) / v / "Bin").is_dir()]
HAS_WIBO = sys.platform == "win32" or toolchains.installed("wibo") is not None


def _cl(pack: str, version: str, argv: list[str]) -> tuple[int, str, bool]:
    home = toolchains.path(pack) / version
    cl = next(p for p in (home / "Bin" / "CL.EXE", home / "Bin" / "cl.exe") if p.exists())
    run = [] if sys.platform == "win32" else [str(toolchains.path("wibo") / "wibo")]
    with tempfile.TemporaryDirectory() as tmp:  # (a run of its own: some write a .pdb where they run)
        Path(tmp, "t.c").write_text(C_FILE)
        done = subprocess.run([*run, str(cl), "/nologo", "/c", "/O2", *argv, "t.c", "/Fot.obj"], cwd=tmp,
                              capture_output=True, text=True, timeout=120,
                              env={**__import__("os").environ, "INCLUDE": str(home / "Include")})
        return done.returncode, done.stdout + done.stderr, Path(tmp, "t.obj").is_file()


# 6.0-7.1 take /openmp without a word, but build no OpenMP (`#pragma omp` warns C4068, unknown pragma, as without it)
SILENT = {"/openmp"}


def _unknown(out: str) -> bool:
    return any(code in out for code in ("D4002", "D9002", "D4014", "D2016", "D8016", "D8021"))  # (or an error)


@pytest.mark.skipif(not INSTALLED or not HAS_WIBO, reason="needs Visual C++ (msvc or msvc2005, proprietary) and wibo")
@pytest.mark.parametrize("pack,version", INSTALLED)
def test_each_visual_cpp_takes_what_the_catalogue_says(pack, version):
    """What clflags.py says each version takes, it does (it builds, maybe saying it's deprecated); the catalogue's
    flags it says the version doesn't take (where their boundaries were run) the compiler says it doesn't know."""
    key = clflags.version_key(version)
    entries = [f for f in clflags.CATALOGUE if f.ran and "x86" in f.targets]
    with ThreadPoolExecutor(8) as pool:
        runs = list(pool.map(lambda f: _cl(pack, version, [*([f.needs] if f.needs else []), f.flag]), entries))
    wrong = []
    for f, (rc, out, made) in zip(entries, runs):
        takes = f.takes(key, "x86")
        rc = rc or int(not made)  # (an object or it didn't build)
        silent = f.flag in SILENT and key < (8, 0, 0)
        if takes and (rc != 0 or _unknown(out)) or not takes and not silent and rc == 0 and not _unknown(out) \
                and "D9035" not in out:  # (2005 and later still build /GX, saying it's deprecated)
            wrong.append(f"{f.flag}: catalogue says {'yes' if takes else 'no'}, cl.exe: {out.strip()[:160]!r}")
    assert not wrong, f"Visual C++ {version}:\n" + "\n".join(wrong)
