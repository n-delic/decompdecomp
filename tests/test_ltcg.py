"""Link-time code generation (LTCG): games built with /GL and /LTCG, judged through a harness link.

A /GL object holds the compiler's intermediate code, so the project links each unit with a driver and stand-ins into
a DLL with /LTCG (the template's tools/ltcg.py, Visual C++'s linker through wine), and tools/dll2coff.py cuts the
unit's functions back out into the object objdiff compares, with an address mapping check objdiff doesn't make. The
unit tests here run dll2coff on a hand-made DLL; the end-to-end tests import the LTCG build of the Xbox demo
(projects/demo-xbox, `build.py ltcg`), where the linker passes arguments in registers, and run only where Visual C++,
wibo, wine, Ghidra and objdiff are installed.
"""

import importlib.util
import json
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from decompnet import toolchains, wine
from decompnet.app.engine import RomNetwork
from decompnet.consoles.pc import scratch as scratch_pe
from decompnet.consoles.pc.scratch import DIR32, REL32
from decompnet.forks import ltcg, objdiff

TOOLS = scratch_pe.TEMPLATE / "tools"
DEMO = Path(__file__).resolve().parent.parent / "projects" / "demo-xbox"


def _tool(name: str):
    sys.path.insert(0, str(TOOLS))
    spec = importlib.util.spec_from_file_location(f"template_{name}", TOOLS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod



dll2coff = _tool("dll2coff")
template_ltcg = _tool("ltcg")


BASE = 0x10000000
# Ours (the harness DLL): F1 reads g_a twice and calls F2; F2 reads a D_ global and switches through a jump table.
F1 = bytes.fromhex("a1 00200010" "03 05 00200010" "e8 10000000" "c3")  # call: 0x1000100b + 5 + 0x10 = 0x10001020
F2 = bytes.fromhex("8b 0d 04200010" "ff 24 85 30100010" "c3" "c3" "90" "2d100010" "2e100010")
RELOC_SITES = [0x1001, 0x1007, 0x1022, 0x1029, 0x1030, 0x1034]
MAP = """ Address         Publics by Value              Rva+Base       Lib:Object

 0001:00000000       ?F1@@YAHXZ                 10001000 f   a.obj
 0001:00000020       ?F2@@YAHH@Z                10001020 f   a.obj
 0002:00000000       ?g_a@@3HA                  10002000     a.obj
 0002:00000004       _D_00030004                10002004     a.obj
"""


def _pe(sections: list[tuple[str, int, bytes]], relocs: list[int]) -> bytes:
    """A minimal PE32 DLL at BASE: the fields dll2coff reads, the sections, and a base relocation table (`relocs`:
    the relocated words' addresses relative to BASE)."""
    pages: dict[int, list[int]] = {}
    for r in relocs:
        pages.setdefault(r & ~0xFFF, []).append(r & 0xFFF)
    table = b""
    for page, offs in sorted(pages.items()):
        entries = b"".join(struct.pack("<H", 0x3000 | o) for o in offs) + (b"\0\0" if len(offs) % 2 else b"")
        table += struct.pack("<II", page, 8 + len(entries)) + entries
    sections = [*sections, (".reloc", 0x1000 * (len(sections) + 1), table)]
    count = len(sections)
    head = bytearray(0x400)
    head[:2] = b"MZ"
    struct.pack_into("<I", head, 0x3C, 0x40)
    head[0x40:0x44] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", head, 0x44, 0x14C, count, 0, 0, 0, 0xE0, 0x2102)
    opt = 0x58
    struct.pack_into("<H", head, opt, 0x10B)
    struct.pack_into("<I", head, opt + 28, BASE)
    struct.pack_into("<I", head, opt + 56, 0x1000 * (count + 1))
    struct.pack_into("<I", head, opt + 92, 16)
    struct.pack_into("<II", head, opt + 96 + 5 * 8, sections[-1][1], len(table))
    body = bytearray()
    for i, (name, rva, data) in enumerate(sections):
        raw = 0x400 + len(body)
        struct.pack_into("<8sIIII", head, opt + 0xE0 + 40 * i, name.encode(), len(data), rva, 0x200, raw)
        body += data.ljust(0x200, b"\0")
    return bytes(head) + bytes(body)


def _dll() -> bytes:
    text = F1 + b"\xcc" * (0x20 - len(F1)) + F2 + b"\xcc" * 8
    return _pe([(".text", 0x1000, text), (".data", 0x2000, bytes(8))], RELOC_SITES)


def _project(tmp_path: Path, second_global: int = 0x30000) -> tuple[Path, dict]:
    """The original (its bytes and functions.json, as scratch_pe would write them) and the harness DLL and map."""
    f1 = bytes.fromhex("a1 00000300" "03 05") + struct.pack("<I", second_global) + bytes.fromhex("e8 10000000 c3")
    f2 = bytes.fromhex("8b 0d 04000300" "ff 24 85 30100100" "c3" "c3" "90" "2d100100" "2e100100")
    exe = (f1 + b"\xcc" * (0x20 - len(f1)) + f2).ljust(0x40, b"\xcc")  # F1 at 0x11000, F2 at 0x11020
    (tmp_path / "game.bin").write_bytes(exe)
    info = {"exe": str(tmp_path / "game.bin"), "sha1": __import__("hashlib").sha1(exe).hexdigest(), "units": {"u": [
        {"name": "?F1@@YAHXZ", "addr": 0x11000, "offset": 0, "size": len(f1), "relocs": [
            [1, DIR32, "_D_00030000", 0], [7, DIR32, f"_D_{second_global:08X}", 0], [12, REL32, "?F2@@YAHH@Z", 0]]},
        {"name": "?F2@@YAHH@Z", "addr": 0x11020, "offset": 0x20, "size": 0x18, "relocs": [
            [2, DIR32, "_D_00030004", 0], [9, DIR32, None, 0x10], [0x10, DIR32, None, 0x0D], [0x14, DIR32, None, 0x0E]]},
    ]}}
    (tmp_path / "functions.json").write_text(json.dumps(info))
    (tmp_path / "u.dll").write_bytes(_dll())
    (tmp_path / "u.map").write_text(MAP)
    src = tmp_path / "u.cpp"
    src.write_text("// FUNCTION: 0x11000\nint F1()\n{ return 0; }\n\n// FUNCTION: 0x11020\nint F2(int x)\n{ return x; }\n")
    return src, info


def _run_dll2coff(tmp_path: Path, src: Path) -> tuple[bytes, dict]:
    out = tmp_path / "u.obj"
    subprocess.run([sys.executable, str(TOOLS / "dll2coff.py"), str(tmp_path / "functions.json"), "u",
                    str(tmp_path / "u.dll"), str(tmp_path / "u.map"), str(out), str(src)], check=True, cwd=tmp_path)
    return out.read_bytes(), json.loads(out.with_suffix(".ltcg.json").read_text())


def _coff(data: bytes) -> tuple[list[tuple[int, list[tuple[int, int, int]]]], list[str]]:
    """(sections: (size, [(offset, symbol index, type)]), symbol names) of a COFF object."""
    count, sym_at, nsyms = struct.unpack_from("<HxxxxII", data, 2)
    strings = sym_at + 18 * nsyms
    names = []
    for i in range(nsyms):
        raw = data[sym_at + 18 * i:sym_at + 18 * i + 8]
        if raw[:4] == b"\0\0\0\0":
            off = strings + struct.unpack_from("<I", raw, 4)[0]
            names.append(data[off:data.index(b"\0", off)].decode())
        else:
            names.append(raw.rstrip(b"\0").decode())
    sections = []
    for i in range(count):
        size, raw, rel_at, _, nrel = struct.unpack_from("<IIIIH", data, 20 + 40 * i + 16)
        sections.append((size, [struct.unpack_from("<IIH", data, rel_at + 10 * k) for k in range(nrel)]))
    return sections, names



def test_functions_are_cut_from_the_dll_by_their_annotations_with_the_originals_layout(tmp_path):
    src, _ = _project(tmp_path)
    data, report = _run_dll2coff(tmp_path, src)
    sections, names = _coff(data)
    # one section per function, from its symbol to the next less the int3 padding; the jump table stays inside F2
    assert [s for s, _ in sections] == [len(F1), 0x18]
    f1 = [(off, names[i], t) for off, i, t in sections[0][1]]
    assert f1 == [(1, "?g_a@@3HA", DIR32), (7, "?g_a@@3HA", DIR32), (12, "?F2@@YAHH@Z", REL32)]
    f2 = [(off, names[i], t) for off, i, t in sections[1][1]]
    # the table's address and its entries are labels inside the function, named as tools/delink.py names them
    assert f2 == [(2, "_D_00030004", DIR32), (9, "$L00011030", DIR32), (0x10, "$L0001102D", DIR32),
                  (0x14, "$L0001102E", DIR32)]
    assert names[:2] == ["?F1@@YAHXZ", "?F2@@YAHH@Z"]  # the original's symbols, so objdiff pairs them
    assert report["functions"]["?F1@@YAHXZ"]["conflicts"] == [] and report["functions"]["?F1@@YAHXZ"]["pairs"] == 3
    assert report["functions"]["?F2@@YAHH@Z"]["conflicts"] == [] and report["problems"] == []


def test_a_split_global_fails_the_address_mapping_check_objdiff_would_pass(tmp_path):
    # the original reads two globals where ours reads one twice: same instructions, different meaning
    src, _ = _project(tmp_path, second_global=0x30008)
    _, report = _run_dll2coff(tmp_path, src)
    assert report["functions"]["?F1@@YAHXZ"]["conflicts"] == ["yours 0x10002000 <- orig 0x30000 and 0x30008"]
    base = tmp_path / "u.obj"
    found = objdiff.mapping_conflicts(base, "?F1@@YAHXZ")
    assert [m.describe() for m in found] == ["yours 0x10002000 <- orig 0x30000 and 0x30008"]
    assert objdiff.mapping_conflicts(base, "?F2@@YAHH@Z") == []


def test_swapped_globals_keep_the_mapping_but_fail_the_data_check(tmp_path):
    # F2's D_ global names 0x30008 where the original reads 0x30004: one reference each, so the mapping is fine
    src, _ = _project(tmp_path)
    (tmp_path / "u.map").write_text(MAP.replace("_D_00030004", "_D_00030008"))
    _, report = _run_dll2coff(tmp_path, src)
    f2 = report["functions"]["?F2@@YAHH@Z"]
    assert f2["conflicts"] == [] and f2["data_checked"] == 1
    assert f2["data"] == ["+0x2: yours refers to _D_00030008 (0x30008) where the original refers to 0x30004"]
    found = objdiff.mapping_conflicts(tmp_path / "u.obj", "?F2@@YAHH@Z")
    assert [type(m) for m in found] == [objdiff.DataConflict]
    d = objdiff.FunctionDiff("?F2@@YAHH@Z", 100.0, [], 0, {}, [], {}, literals=found)
    assert not d.matched and "names another original address" in d.literals_note()
    assert d.why_not().endswith("refers to other globals or callees than the original")


def test_a_unit_joins_harness_mode_once_its_driver_defines_harness_entry(tmp_path):
    h = tmp_path / "harness"
    h.mkdir()
    (h / "u.drv.cpp").write_text(ltcg.DRIVER.format(unit="u"))
    (h / "u.standins.cpp").write_text('// HARNESS-LINK: d3d8ltcg.lib /d2:"-inlT56"\n')
    (h / "u2.drv.cpp").write_text("")
    driver, extras = template_ltcg.harness_files("u", h)
    assert extras == [h / "u.standins.cpp"]
    assert not template_ltcg.has_entry(driver)  # the skeleton's entry is commented out
    driver.write_text('extern "C" __declspec(dllexport) void HarnessEntry(int *p)\n{\n}\n')
    assert template_ltcg.has_entry(driver)
    assert template_ltcg.link_flags([driver, *extras]) == ["d3d8ltcg.lib", "/d2:-inlT56"]


def test_an_ltcg_project_gets_the_setting_and_a_driver_per_game_unit(tmp_path):
    shutil.copytree(TOOLS, tmp_path / "tools")

    class Fn:
        def __init__(self, addr):
            self.addr = addr

    ltcg.prepare(tmp_path, {"text_00011000": [Fn(0x11000)], "text_001D0000": [Fn(0x1D0000)]}, library={0x1D0000})
    assert "\nLTCG = True\n" in (tmp_path / "tools" / "configure.py").read_text()
    assert (tmp_path / "harness" / "text_00011000.drv.cpp").read_text().startswith("// Harness driver for")
    assert not (tmp_path / "harness" / "text_001D0000.drv.cpp").exists()  # the XDK's own code is set aside
    assert "/GL" not in ltcg.XBOX_CL_FLAGS and "/Ob1" in ltcg.XBOX_CL_FLAGS


def test_tools_brought_up_to_date_keep_a_projects_ltcg_setting(tmp_path):
    root = tmp_path / "p"
    shutil.copytree(TOOLS.parent, root)
    cfg = root / "tools" / "configure.py"
    cfg.write_text(cfg.read_text().replace("\nLTCG = False\n", "\nLTCG = True\n"))
    (root / "tools" / "ltcg.py").unlink()  # (a project made before the harness tools)
    (root / "config" / "v").mkdir(parents=True)
    assert "tools/ltcg.py" in scratch_pe.write_tools(root, "v")
    assert "\nLTCG = True\n" in cfg.read_text()


# A program for the detection: C takes `this` in ESI (LTCG's choice) and one stack argument (ret 4); A loads ESI and
# pushes before calling it; S is a __thiscall method that saves ESI.
CONV_CODE = {
    0x1000: bytes.fromhex("8b4604" "03442404" "c20400"),  # C: mov eax, [esi+4]; add eax, [esp+4]; ret 4
    0x1010: bytes.fromhex("be00200010" "6a05" "e8e4ffffff" "a104200010" "c3"),  # A: mov esi, D; push 5; call C
    0x1030: bytes.fromhex("56" "8bf1" "8b06" "5e" "c3"),  # S: push esi; mov esi, ecx; mov eax, [esi]; pop esi
    0x1040: bytes.fromhex("b900200010" "e8e6ffffff" "c3"),  # B: mov ecx, D; call S
}


def test_ltcg_is_detected_from_call_sites_that_load_registers_no_standard_convention_uses(tmp_path, monkeypatch):
    from decompnet.forks import convention

    text = bytearray(b"\xcc" * 0x60)
    for at, code in CONV_CODE.items():
        text[at - 0x1000:at - 0x1000 + len(code)] = code
    (tmp_path / "orig").mkdir()
    (tmp_path / "orig" / "game.exe").write_bytes(_pe([(".text", 0x1000, bytes(text)), (".data", 0x2000, bytes(8))], []))

    def fn(name, at, relocs):
        return {"name": name, "addr": BASE + at, "size": len(CONV_CODE[at]), "relocs": relocs}

    info = {"units": {"u": [fn("_func_10001010", 0x1010, [[1, DIR32, "_D_10002000", 0], [8, REL32, "_C", 0],
                                                          [13, DIR32, "_D_10002004", 0]]),
                            fn("_func_10001040", 0x1040, [[1, DIR32, "_D_10002000", 0], [6, REL32, "_S", 0]])],
                      "v": [fn("_C", 0x1000, []), fn("_S", 0x1030, [])]}}
    (tmp_path / "config" / "game").mkdir(parents=True)
    (tmp_path / "config" / "game" / "functions.json").write_text(json.dumps(info))
    prog = convention.load(tmp_path, {"version": "game", "game": {"path": "orig/game.exe"}})
    monkeypatch.setattr(convention, "MIN_CONFIRMED", 1)
    found = convention.detect(prog)
    # C (ESI, loaded by its caller) counts; S's ECX is __thiscall's own
    assert (found["ltcg"], found["confirmed"], found["sampled"]) == (True, 1, 2)
    assert "_C reads esi before writing it, and _func_10001010+0x7 sets it up: mov esi, 0x10002000" in found["examples"][0]
    assert convention.detect(prog, skip={BASE + 0x1000})["ltcg"] is False  # S alone: a plain program
    assert "register parameters of LTCG's choosing: esi" in "\n".join(convention.evidence(prog, BASE + 0x1000))


# ------------------------------------------------------------------------ the demo, built with /GL and /LTCG

ABS_S32 = "_abs_s32"  # a function of the demo whose argument the linker passes in EAX
DRIVER = """#include "common.h"

s32 func_{address:08X}(s32 v);
extern "C" __declspec(dllexport) s32 HarnessEntry(s32 v)
{{
    return func_{address:08X}(v) + 1;
}}
"""
ABS_C = """s32 func_{address:08X}(s32 v)
{{
    return v < 0 ? -v : v;
}}
"""


def _installed(*tools) -> bool:
    return all(toolchains.installed(t) is not None for t in tools)


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    """The demo built with /GL and /LTCG (build.py ltcg), its linker run through wine in a prefix of the test's own
    (never the app's), which the project's builds find through $DECOMPNET_WINE_PREFIX too."""
    if not _installed("msvc", "wibo", "objdiff-cli", "ghidra") or wine.find() is None:
        pytest.skip("the LTCG demo needs Visual C++ (msvc), wibo, wine, Ghidra and objdiff")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DECOMPNET_WINE_PREFIX", str(tmp_path_factory.mktemp("wine")))
        key = DEMO / "build" / "ltcg" / "demo.json"
        if not key.exists():
            subprocess.run([sys.executable, str(DEMO / "build.py"), "ltcg"], check=True)
        built = json.loads(key.read_text())
        yield DEMO / "build" / "ltcg" / built["game"], built["symbols"]


@pytest.fixture(scope="module")
def project(demo, tmp_path_factory):
    work = tmp_path_factory.mktemp("ltcg")
    net = RomNetwork(work / "app.db", work)
    said: list[str] = []
    slug = net.import_rom(demo[0], confirmed=True, log=said.append)
    return net, slug, said


def test_an_import_of_a_game_built_with_ltcg_detects_it_and_writes_the_harness_files(demo, project):
    net, slug, said = project
    assert any(line.startswith("link-time code generation? ") and ": link-time code generation\n" in line
               for line in said), said
    fp = net.forks.get(slug)[1]
    assert fp.manifest["ltcg"] is True and fp.manifest["ltcg_detected"]["ltcg"] is True
    assert "\nLTCG = True\n" in (fp.root / "tools" / "configure.py").read_text()
    assert "/Ob1" in (fp.root / "tools" / "configure.py").read_text()
    drivers = list((fp.root / "harness").glob("*.drv.cpp"))
    assert drivers and "HarnessEntry" in drivers[0].read_text()
    assert not template_ltcg.has_entry(drivers[0])  # (a unit builds in harness mode once its driver defines it)


def _function(net, slug, symbols, symbol):
    """(the function's name in the project, its unit, its address)."""
    address = symbols[symbol]
    pid = net.project_row(slug)["id"]
    name = next(r["name"] for r in net.store.all("SELECT name FROM functions WHERE project_id=?", pid)
                if f"{address:08X}" in r["name"].upper())
    fp = net.forks.get(slug)[1]
    info = json.loads((fp.root / "config" / "demoquest" / "functions.json").read_text())
    unit = next(u for u, fns in info["units"].items() if any(fn["addr"] == address for fn in fns))
    return name, unit, address


def test_a_function_with_a_register_convention_matches_through_the_harness_and_wrong_c_does_not(demo, project):
    net, slug, said = project
    name, unit, address = _function(net, slug, demo[1], ABS_S32)
    fp = net.forks.get(slug)[1]
    agent, _ = net.local_agent("ltcg-agent")
    net.claim(agent, slug, name)
    # without a driver the unit compiles alone (no /GL): the standard convention, not the game's
    alone = net.attempt(agent, name, ABS_C.format(address=address), slug)
    assert not alone["matched"], alone["text"]
    # with a driver calling it, the link gives it the game's convention (the argument in EAX)
    (fp.root / "harness" / f"{unit}.drv.cpp").write_text(DRIVER.format(address=address))
    wrong = net.attempt(agent, name, ABS_C.format(address=address).replace("-v : v", "-v : v + 1"), slug)
    assert not wrong["matched"] and "BUILD ERROR" not in wrong["text"] and "% match" in wrong["text"], wrong["text"]
    right = net.attempt(agent, name, ABS_C.format(address=address), slug)
    assert right["matched"], right["text"]
    report = json.loads((fp.root / "build" / "demoquest" / "src" / "auto" / f"{unit}.ltcg.json").read_text())
    assert report["functions"][name]["conflicts"] == [] and report["problems"] == []


@pytest.mark.skipif(sys.platform == "win32", reason="Windows runs the linker itself")
def test_an_xbox_build_is_given_wine_where_it_is_and_a_pc_build_only_when_a_pack_needs_it(tmp_path, monkeypatch):
    from decompnet import consoles

    monkeypatch.setattr(wine, "find", lambda: "/usr/bin/wine")
    monkeypatch.setenv("DECOMPNET_WINE_PREFIX", str(tmp_path))
    assert consoles.get("xbox").build_env() == {"DECOMPNET_WINE": "/usr/bin/wine", "DECOMPNET_WINE_PREFIX": str(tmp_path)}
    monkeypatch.setattr(toolchains, "installed", lambda tid: None)
    assert consoles.get("pc").build_env() == {}


@pytest.fixture(scope="module")
def fresh(demo, tmp_path_factory):
    """Another import of the demo, whose driver nobody has written (the tests above write the first project's)."""
    work = tmp_path_factory.mktemp("ltcg-agent")
    net = RomNetwork(work / "app.db", work)
    return net, net.import_rom(demo[0], confirmed=True, log=lambda *_: None)


def test_an_agent_goes_from_the_brief_to_a_match_with_the_tools_it_has(demo, fresh):
    """Claim, read the convention in the brief, ask standins_for for a starting harness, write the driver with
    edit_file, and compile: the way an agent works an LTCG game's register-convention function."""
    from decompnet.app import tools

    net, slug = fresh
    name, unit, address = _function(net, slug, demo[1], ABS_S32)
    assert "standins_for" in tools.project_tools(net, slug)
    agent, _ = net.local_agent("ltcg-agent")
    brief = net.claim(agent, slug, name)
    assert "reads before writing: eax" in brief and "register parameters of LTCG's choosing: eax" in brief
    assert "called from" in brief and "builds plain, without /GL" in brief and "standins_for" in brief
    starting = tools.HANDLERS["standins_for"](net, agent, slug, name)
    assert f"harness/{unit}.drv.cpp" in starting and "func_%08X" % address in starting
    assert "func_%08X: reads before writing: eax" % address in starting
    alone = net.attempt(agent, name, ABS_C.format(address=address), slug)
    assert not alone["matched"]
    said = tools.HANDLERS["edit_file"](net, agent, slug, f"harness/{unit}.drv.cpp", "",
                                       DRIVER.format(address=address).replace('#include "common.h"\n', ""), "driver")
    assert not said.startswith("❌"), said
    assert "already goes through the harness" in net.get_function(name, slug, agent)
    right = net.attempt(agent, name, ABS_C.format(address=address), slug)
    assert right["matched"], right["text"]
    # the rebuild check puts the harness-made object back into the program, byte for byte
    from decompnet import consoles

    fp = net.forks.get(slug)[1]
    rebuilt = consoles.get("xbox").check_rebuild(fp, fp.manifest["version"])
    assert rebuilt["identical"] and "1 of them from C" in rebuilt["detail"], rebuilt
    # what the harness link left unresolved reaches the agent by kind (here a made-up report of the unit's link)
    built_unit = fp.unit(unit)
    side = built_unit.base.with_suffix(".ltcg.json")
    side.write_text(json.dumps({**json.loads(side.read_text()), "unresolved": ["_D_00013400", "_func_000113C0@8"]}))
    row = net.forks._fn(slug, name)
    text = "\n".join(net.forks._unresolved_lines(fp, net.forks._meta(row), row, built_unit,
                                                 "void f() { D_00013400 = 1; func_000113C0(); }"))
    assert "D_00013400 (data: may change this function's code under LTCG" in text
    assert "func_000113C0 (" in text and "standins_for" in text


def test_the_import_asks_whether_the_game_used_ltcg_through_the_consoles_options(tmp_path):
    from decompnet import consoles
    from decompnet.app import tools

    xbox = consoles.get("xbox")
    asked = xbox.describe()["import_options"]
    assert [o["id"] for o in asked] == ["ltcg"] and [c["value"] for c in asked[0]["choices"]] == ["auto", "on", "off"]
    assert consoles.get("pc").describe()["import_options"] == []

    seen = {}

    class Forks:
        def create_scratch(self, path, console, **kwargs):
            seen.update(kwargs)
            return "slug"

    net = type("Net", (), {"forks": Forks(), "saved_file": lambda self, data, name: tmp_path / name})()
    xbox.import_game(net, tmp_path / "default.xbe", options={"ltcg": "off", "nonsense": "1"})
    assert seen["options"] == {"ltcg": "off"}  # (answers to questions the console doesn't ask are dropped)
    assert "standins_for" in tools.tool_names(True, xbox, ltcg=True)
    assert "standins_for" not in tools.tool_names(True, xbox) and "standins_for" not in tools.tool_names(False)


def test_the_user_can_say_the_game_did_not_use_ltcg(demo, tmp_path):
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    said: list[str] = []
    slug = net.import_rom(demo[0], confirmed=True, log=said.append, options={"ltcg": "off"})
    fp = net.forks.get(slug)[1]
    assert not fp.manifest.get("ltcg") and "LTCG = False" in (fp.root / "tools" / "configure.py").read_text()
    assert not any(line.startswith("link-time code generation? ") for line in said)  # (nothing to detect)
    assert not list((fp.root / "harness").glob("*.drv.cpp"))
