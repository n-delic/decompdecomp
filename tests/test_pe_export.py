"""The PC and Xbox export: the game's program rebuilt from the project with the game's own linker (the template's
tools/export.py, `ninja export`), byte for byte.

Every function is a section of its own with its relocations, the bytes between them are carried as they are, and a
function src/ compiles to the original's bytes comes from the compiled object. The unit tests build the pieces of a
hand-made program; the end-to-end tests link a homemade program with Visual C++ (and, for Xbox, the user's own XDK
and its imagebld), take it for "the game", and rebuild it from its project: once from its own code only, once with
its functions from compiled C. They run only where those tools are installed.
"""

import hashlib
import importlib.util
import json
import os
import re
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from decompnet import kits, toolchains, wine
from decompnet.consoles.pc import scratch as scratch_pe
from decompnet.consoles.pc.scratch import DIR32, REL32
from decompnet.consoles.xbox import scratch as scratch_xbe

TOOLS = scratch_pe.TEMPLATE / "tools"


def _tool(name: str):
    spec = importlib.util.spec_from_file_location(f"template_{name}", TOOLS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # its dataclasses look their module up there
    spec.loader.exec_module(mod)
    return mod


export = _tool("export")

# A hand-made program: one function at 0x401000 that reads the int at 0x402000 and calls the next one (0x401010),
# which returns; then .data with that int.
CODE = bytes.fromhex("a1 00204000" "e8 06000000" "c3" "cccccc" "cccc" + "c3" + "cc" * 15)
DATA = struct.pack("<II", 7, 0x401000)


def _program() -> "export.Program":
    data = bytearray(0x400) + CODE.ljust(0x200, b"\0") + DATA.ljust(0x200, b"\0")
    sections = [export.Section(".text", 0x401000, len(CODE), 0x400, len(CODE), False),
                export.Section(".data", 0x402000, len(DATA), 0x600, len(DATA), True)]
    return export.Program("pe", bytes(data), 0x400000, 0x401000, sections)


def _info(relocs_twice: bool = False) -> dict:
    first = [[1, DIR32, "_D_00402000", 0], [6, REL32, "_func_00401010", 0]]
    return {"units": {"text_00401000": [
        {"name": "_func_00401000", "addr": 0x401000, "offset": 0x400, "size": 11,
         "relocs": first + ([first[0]] if relocs_twice else [])},
        {"name": "_func_00401010", "addr": 0x401010, "offset": 0x410, "size": 1, "relocs": []}]}}


def _pieces(tmp_path: Path, compiled: dict | None = None, relocs_twice: bool = False):
    ex = export.Export(_program(), _info(relocs_twice), compiled or {}, None)
    objs = ex.objects(tmp_path)
    return ex, {p.name: export.read_coff(p.read_bytes()) for p in objs}


def test_every_byte_of_the_program_is_in_a_piece_with_its_symbols(tmp_path):
    ex, objs = _pieces(tmp_path)
    (text, tsyms), (data, dsyms) = objs["s00_text_000.obj"], objs["s01_data_001.obj"]
    # .text: the two functions and the padding between them; their bytes together are the section's
    assert [len(s.data) for s in text] == [11, 5, 1, 15]
    joined = bytearray(b"".join(s.data for s in text))
    assert joined[:1] + joined[5:6] + joined[10:] == CODE[:1] + CODE[5:6] + CODE[10:]
    assert joined[1:5] == bytes(4) and joined[6:10] == bytes(4)  # relocation fields: the linker fills them in
    names = {s.name: (s.section, s.value) for s in tsyms.values()}
    assert names["_func_00401000"] == (1, 0) and names["_func_00401010"] == (3, 0) and names["_export_entry"] == (1, 0)
    # the data the code reads is defined at its address; its own words stay as they are
    assert {s.name: (s.section, s.value) for s in dsyms.values()}["_D_00402000"] == (1, 0)
    assert b"".join(s.data for s in data) == DATA


def test_a_relocation_listed_twice_is_linked_once(tmp_path):
    _, objs = _pieces(tmp_path, relocs_twice=True)
    assert [o for o, _, _ in objs["s00_text_000.obj"][0][0].relocs] == [1, 6]


def _compiled(names: list[str], field: bytes = bytes(4)) -> dict:
    """func_00401000 as a compiler would write it: the code, relocations to `names` (the read, the call)."""
    code = CODE[:1] + field + CODE[5:6] + bytes(4) + CODE[10:11]
    symbols = {0: export.ObjSymbol("_func_00401000", 0, 1, 2)}
    symbols |= {i + 1: export.ObjSymbol(n, 0, 0, 2) for i, n in enumerate(names)}
    sec = export.ObjSection(".text", code, [(1, 1, DIR32), (6, 2, REL32)])
    return {"_func_00401000": (sec, symbols, 1)}


def test_a_function_that_compiles_to_the_original_comes_from_the_compiled_object(tmp_path):
    # the call names the game's function; the read names a global the compiler made: it refers to the original's
    ex, objs = _pieces(tmp_path, _compiled(["?s_local@@3HA", "_func_00401010"]))
    assert ex.report["compiled"] == ["_func_00401000"] and ex.report["paired"] == 1
    text, syms = objs["s00_text_000.obj"]
    assert [syms[s].name for _, s, _ in text[0].relocs] == ["_D_00402000", "_func_00401010"]


def test_a_compiled_function_that_refers_to_the_wrong_data_keeps_the_original(tmp_path):
    # objdiff, as decompdecomp runs it, doesn't compare what relocations point at: the export does
    ex, _ = _pieces(tmp_path, _compiled(["_D_00402004", "_func_00401010"]))
    assert ex.report["compiled"] == [] and "_func_00401000+0x1" in ex.report["conflicts"][0]
    ex, _ = _pieces(tmp_path, _compiled(["_D_00402000", "_func_00401010"], field=b"\x05\0\0\0"))  # data+5
    assert ex.report["compiled"] == []
    different = _compiled(["_D_00402000", "_func_00401010"])
    different["_func_00401000"][0].data = b"\x90" + different["_func_00401000"][0].data[1:]
    ex, _ = _pieces(tmp_path, different)
    assert ex.report["compiled"] == [] and ex.report["conflicts"] == []


def test_kernel_imports_are_named_from_the_import_library():
    def member(name: bytes, body: bytes) -> bytes:
        head = name.ljust(16) + b"0".ljust(12) + b"0".ljust(6) + b"0".ljust(6) + b"644".ljust(8) + \
            str(len(body)).encode().ljust(10) + b"`\n"
        return head + body + (b"\n" if len(body) & 1 else b"")

    def short_import(ordinal: int, symbol: bytes) -> bytes:
        rest = symbol + b"\0xboxkrnl.exe\0"
        return struct.pack("<HHHHIIHH", 0, 0xFFFF, 0, 0x14C, 0, len(rest), ordinal, 0) + rest

    lib = b"!<arch>\n" + member(b"/", b"\0\0\0\0") + member(b"xboxkrnl.exe/", short_import(187, b"_NtClose@4")) + \
        member(b"xboxkrnl.exe/", short_import(8, b"_DbgPrint"))
    assert export.import_names(lib) == {187: "__imp__NtClose@4", 8: "__imp__DbgPrint"}


def test_an_xbox_projects_view_gives_its_xbe_to_rebuild():
    """An Xbox project's orig/ program is a Windows view of the XBE (consoles/xbox/scratch.py pe_view): the export
    rebuilds the XBE inside it, as tools/rebuild.py writes it back."""
    xbe = b"XBEH" + bytes(0x200)
    view = bytearray(0x400) + xbe
    view[:2] = b"MZ"
    view[export.XBE_MARK:export.XBE_MARK + 12] = b"XBEH" + struct.pack("<II", 0x400, len(xbe))
    assert export.inner_xbe(bytes(view)) == xbe
    assert export.inner_xbe(b"MZ" + bytes(0x100)) is None  # a PC program's


# ---------------------------------------------------------------------------------------------------- end to end

# The homemade program: a global, a float the compiler keeps in .rdata, a call and a switch with a jump table.
PROGRAM = """int _fltused;  /* what the C runtime defines for code using floats: there's no runtime here */
int g_count = 3;
float g_scale;
int {f1}(int x) {{ return x * g_count + 1; }}
int {f2}(int k)
{{
    switch (k) {{
    case 0: return 10; case 1: return {f1}(k); case 2: return 40; case 3: return g_count;
    case 4: return 7; case 5: return -5; default: return 0;
    }}
}}
float {f3}(float v) {{ g_scale = v * 0.25f; return g_scale; }}
{kernel}
void {entry}(void) {{ g_count = {f2}(g_count) + ({f3}(2.0f) > 1.0f){call}; }}
"""
# An Xbox program: a kernel import, and the library version records the XDK's libraries would give imagebld
KERNEL = """__declspec(dllimport) long __stdcall NtClose(void *h);
#pragma data_seg(".XBLD$V")
struct { char name[8]; unsigned short v[4]; } g_libs[2] = {{"XBOXKRNL", {1, 0, 5849, 0x4001}},
                                                            {"XAPILIB", {1, 0, 5849, 0x4001}}};
#pragma data_seg()
#pragma comment(linker, "/SECTION:.XBLD,D")
"""  # (a value put into PROGRAM, so its braces are single)


def _msvc() -> tuple[Path, Path | None] | None:
    """(Visual C++ 7.1's Bin folder, wibo off Windows) where the homemade program can be built: off Windows wibo runs
    the compiler and wine the linker (export.py links through wine too)."""
    if toolchains.installed("msvc") is None:
        return None
    wibo = None
    if sys.platform != "win32":
        if not toolchains.installed("wibo") or wine.find() is None:
            return None
        wibo = toolchains.binary("wibo")
    return toolchains.path("msvc") / "7.1" / "Bin", wibo


def _xdk() -> Path | None:
    """Your own XDK's xbox folder, as Setup recorded it (the xdk kit)."""
    return kits.folder("xdk")


needs_msvc = pytest.mark.skipif(_msvc() is None, reason="needs Visual C++ (msvc, proprietary) and (off Windows) wibo "
                                                        "and wine")
needs_xdk = pytest.mark.skipif(_msvc() is None or _xdk() is None,
                               reason="needs Visual C++ (msvc), your own XDK set up in Setup and (off Windows) wine")
# what runs the linker and imagebld: nothing on Windows, else wine with the app's prefix
LINK_WRAP = [] if sys.platform == "win32" else [str(wine.find() or "wine")]
LINK_ENV = {**os.environ, **wine.build_env(always=True)}


def _run(wrap: list, exe: Path, args: list[str], cwd: Path, env: dict | None = None) -> None:
    res = subprocess.run([*map(str, wrap), str(exe), *args], cwd=cwd, capture_output=True, text=True,
                         errors="replace", timeout=600, env=env, stdin=subprocess.DEVNULL)
    assert res.returncode == 0, res.stdout + res.stderr


def _build(work: Path, xbox: bool, names: dict[str, str], program: str = PROGRAM) -> Path:
    """The homemade program linked as a game would be (an Xbox one: then made an XBE by imagebld). A `program` with
    a library section (LIBRARY_PROGRAM) is compiled a second time without it into src.obj, as src/ compiles."""
    bin_, wibo = _msvc()
    xdk = _xdk() if xbox else None
    src = work / "game.c"  # C: the functions' symbols are the import's names as they are (_func_00401000)
    src.write_text(program.format(**names, kernel=KERNEL if xbox else "", call=" + (int)NtClose(0)" if xbox else ""),
                   encoding="utf-8")
    # /Gy: a section per function; /Ob1: the calls stay calls (no function is inlined away)
    flags = [*(scratch_xbe.CL_FLAGS if xbox else ["/nologo", "/c", "/O2", "/Gy", "/W3"]), "/Ob1"]
    if xbox and sys.platform == "win32":  # the XDK's own 7.1 (the same build) runs on Windows with its runtime beside it
        bin_ = _xdk_linker(xdk).parent
    cc = [wibo] if wibo else []
    _run(cc, bin_ / "cl.exe", [*flags, "/DLIBRARY_SECTION", "game.c", "/Fogame.obj"], work)
    if program is not PROGRAM:
        _run(cc, bin_ / "cl.exe", [*flags, "game.c", "/Fosrc.obj"], work)
    entry = names["entry"]
    # (relative paths: through wine an absolute POSIX path starts with a slash, which the linker reads as an option)
    if xbox:
        _run(LINK_WRAP, _xdk_linker(xdk), ["/nologo", "/SUBSYSTEM:XBOX", "/NODEFAULTLIB", f"/ENTRY:{entry}",
                                           "/MAP:game.map", "/OUT:game.exe", "game.obj",
                                           os.path.relpath(xdk / "lib" / "xboxkrnl.lib", work)], work, LINK_ENV)
        _run(LINK_WRAP, xdk / "bin" / "imagebld.exe", ["/NOLOGO", "/NOMAP", "/IN:game.exe", "/OUT:game.xbe",
                                                       "/NOLIBWARN"], work, LINK_ENV)
        return work / "game.xbe"
    _run(LINK_WRAP, bin_ / "link.exe", ["/nologo", "/SUBSYSTEM:CONSOLE", "/NODEFAULTLIB", f"/ENTRY:{entry}", "/FIXED",
                                        "/MAP:game.map", "/OUT:game.exe", "game.obj"], work, LINK_ENV)
    return work / "game.exe"


def _xdk_linker(xdk: Path) -> Path:
    """The XDK's own linker (its vc71 or vc7 folder), which knows /SUBSYSTEM:XBOX."""
    return next(p for sub in ("vc71", "vc7") for name in ("Link.Exe", "link.exe", "LINK.EXE")
                if (p := xdk / "bin" / sub / name).exists())


def _map_functions(path: Path, pe) -> list[int]:
    """The functions of the link's map (its `f` lines), at their section's address in `pe`: by section name and
    offset, as imagebld lays an XBE out at other addresses than the PE the link wrote and mapped."""
    text = path.read_text(encoding="utf-8", errors="replace")
    names = {}  # the map's section number -> the program's section name (`.text$x` groups go in `.text`)
    for m in re.finditer(r"^\s*([0-9a-fA-F]{4}):[0-9a-fA-F]{8}\s+[0-9a-fA-F]+H\s+(\S+)", text, re.M):
        names.setdefault(int(m.group(1), 16), m.group(2).split("$")[0])
    starts = set()
    for m in re.finditer(r"^\s*([0-9a-fA-F]{4}):([0-9a-fA-F]{8})\s+\S+\s+[0-9a-fA-F]{8} f ", text, re.M):
        sec = next((s for s in pe.sections if s.name == names.get(int(m.group(1), 16))), None)
        if sec is not None:
            starts.add(sec.va + int(m.group(2), 16))
    return sorted(starts)


def _project(work: Path, program: Path, xbox: bool) -> tuple[Path, list[scratch_pe.Function]]:
    """config/game/functions.json for the program, as an import writes it: an XBE as the Windows view the Xbox import
    makes of it (orig/), its functions where the link's map puts them, analysed as the PC path does."""
    data = program.read_bytes()
    if xbox:  # (the project's program is the view, as consoles/xbox makes it)
        data = scratch_xbe.pe_view(data)
        program = work / "orig.exe"
        program.write_bytes(data)
    pe = scratch_pe.read_pe(data)
    starts = _map_functions(work / "game.map", pe)
    fns = scratch_pe.analyse(pe, [{"entry": a} for a in starts])
    cfg = work / "config" / "game"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "functions.json").write_text(json.dumps({
        "exe": program.name, "sha1": hashlib.sha1(data).hexdigest(), "image_base": pe.image_base,
        "units": {u: [{"name": f.name, "addr": f.addr, "offset": pe.offset(f.addr), "size": f.size,
                       "relocs": [list(r) for r in f.relocs]} for f in group]
                  for u, group in scratch_pe.units(fns).items()}}), encoding="utf-8")
    return cfg / "functions.json", fns


def _export(work: Path, config: Path, xbox: bool, *extra: str) -> dict:
    bin_, _ = _msvc()
    xdk = _xdk() if xbox else None
    link = _xdk_linker(xdk) if xbox else bin_ / "link.exe"
    res = subprocess.run([sys.executable, str(TOOLS / "export.py"), str(config.relative_to(work)), "out",
                          "--link", str(link), *(["--xdk", str(xdk)] if xdk else []), *extra],
                         cwd=work, capture_output=True, text=True, errors="replace", timeout=900, env=LINK_ENV)
    assert (work / "out" / "export.json").exists(), res.stdout + res.stderr
    return json.loads((work / "out" / "export.json").read_text(encoding="utf-8"))


def _rebuilds(tmp_path: Path, xbox: bool) -> None:
    # once with any names, to learn where the functions land; then the same code named as the import names them
    first = tmp_path / "first"
    first.mkdir()
    names = {"f1": "one", "f2": "two", "f3": "three", "entry": "start"}
    _, fns = _project(first, _build(first, xbox, names), xbox)
    order = sorted(fns, key=lambda f: f.addr)
    assert len(order) >= 4, [f.name for f in order]
    work = tmp_path / "game"
    work.mkdir()
    named = dict(zip(["f1", "f2", "f3"], [f"func_{f.addr:08X}" for f in order[:3]]))
    named["entry"] = f"func_{order[3].addr:08X}"
    config, fns = _project(work, _build(work, xbox, named), xbox)

    alone = _export(work, config, xbox, "--delinked")
    assert alone["identical"], alone["differences"]
    assert alone["kernel_imports"] == ("xboxkrnl.lib" if xbox else "the original's table")

    # the program's own object stands in for what src/ compiles to (its C names are the import's)
    (work / "objdiff.json").write_text(json.dumps({"units": [{"name": "game", "base_path": "game.obj"}]}),
                                       encoding="utf-8")
    built = _export(work, config, xbox)
    assert built["identical"], built["differences"]
    assert len(built["compiled"]) == 4 and built["paired"] >= 2, built  # g_count, g_scale, 0.25f, ...


@needs_msvc
def test_a_pc_program_rebuilds_byte_for_byte(tmp_path):
    _rebuilds(tmp_path, xbox=False)


def test_a_function_the_ltcg_checks_fail_is_never_taken_from_src(tmp_path):
    # OnlineListGames-like: its bytes and relocations agree with the original's, but it pushes itself as the
    # callback where the original pushes another function; only dll2coff's mapping check sees that
    delink = _tool("delink")
    code = bytes.fromhex("68 00100010 c3")
    fns = [{"name": n, "addr": a, "offset": 0, "size": 6, "relocs": [[1, DIR32, "_func_00011100", 0]]}
           for n, a in (("_func_00011000", 0x11000), ("_func_00011010", 0x11010))]
    obj = tmp_path / "u.obj"
    obj.write_bytes(delink.coff(fns, code))
    (tmp_path / "objdiff.json").write_text(json.dumps({"units": [{"name": "u", "base_path": str(obj)}]}))
    assert set(export.compiled_functions(tmp_path / "objdiff.json")) == {"_func_00011000", "_func_00011010"}
    obj.with_suffix(".ltcg.json").write_text(json.dumps({"functions": {
        "_func_00011000": {"conflicts": ["+0x1: yours refers to this function itself, the original to 0x11100"],
                           "data": []},
        "_func_00011010": {"conflicts": [], "data": [], "folded": ["orig 0x3B11A0 -> ..."]}}}))
    assert set(export.compiled_functions(tmp_path / "objdiff.json")) == {"_func_00011010"}


@needs_xdk
def test_an_xbox_program_rebuilds_byte_for_byte(tmp_path):
    _rebuilds(tmp_path, xbox=True)


# An Xbox program with code in a library section of its own, as the XDK's big libraries link theirs (XPP here): two
# functions there, each called from two places (what tells the import it's x86 code), called from .text.
LIBRARY_PROGRAM = """int g_count = 3;
#ifdef LIBRARY_SECTION
#pragma code_seg("XPP")
#endif
int {lib1}(int x) {{ return x * g_count + 1; }}
int {lib2}(int x) {{ return {lib1}(x) + {lib1}(x + 3); }}
#ifdef LIBRARY_SECTION
#pragma code_seg()
#endif
int {f1}(int k) {{ return {lib2}(k) + {lib2}(k * 2); }}
{kernel}
void {entry}(void) {{ g_count = {f1}(g_count){call}; }}
"""


@needs_xdk
def test_an_xbox_program_with_code_in_a_library_section_rebuilds_byte_for_byte(tmp_path):
    """Functions outside .text are pieces of their own section (its name, in the code group) like .text's, from
    the delinked original or from what src/ compiles (into .text, as agents' C does)."""
    first = tmp_path / "first"
    first.mkdir()
    program = _build(first, True, {"lib1": "one", "lib2": "two", "f1": "three", "entry": "start"}, LIBRARY_PROGRAM)
    _, fns = _project(first, program, True)
    view = scratch_pe.read_pe((first / "orig.exe").read_bytes())
    lib = [f for f in fns if view.section_at(f.addr).name == "XPP"]
    text = [f for f in fns if view.section_at(f.addr).name == ".text"]
    assert len(lib) == 2 and len(text) == 2, [(f.name, view.section_at(f.addr).name) for f in fns]
    work = tmp_path / "game"
    work.mkdir()
    named = {k: f"func_{f.addr:08X}" for k, f in zip(["lib1", "lib2", "f1", "entry"], [*lib, *text])}
    config, fns = _project(work, _build(work, True, named, LIBRARY_PROGRAM), True)
    assert {f.name for f in fns} == {f"_{n}" for n in named.values()}

    alone = _export(work, config, True, "--delinked")
    assert alone["identical"], alone["differences"]
    (work / "objdiff.json").write_text(json.dumps({"units": [{"name": "game", "base_path": "src.obj"}]}),
                                       encoding="utf-8")
    built = _export(work, config, True)
    assert built["identical"], built["differences"]
    assert sorted(built["compiled"]) == sorted(f"_{n}" for n in named.values()), built
