"""PC (Windows) from scratch: a project generated for the user's .exe from Ghidra's analysis, worked on like a fork.

Demo Quest PC (projects/demo-pc) is built like a PC game of its time: Visual C++ 6.0 /O2, linked at 0x400000 without
relocations, with __stdcall and __fastcall functions, a jump table and a hand-written assembly helper. Importing it
must find every function with its exact extent, and agents' C must be judged by the project's own build and objdiff.
Visual C++ is proprietary, so the end-to-end part runs only where `msvc` was installed with its license accepted.
"""

import hashlib
import json
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from decompnet import consoles, toolchains
from decompnet.consoles.pc import scratch as scratch_pe
from decompnet.forks import csource, ghidra
from decompnet.forks.project import ForkError, ForkProject
from decompnet.network import NetworkError

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "projects" / "demo-pc"
EXE, MAP = DEMO / "build" / "demoquest.exe", DEMO / "build" / "symbols.map"
GAME = ROOT / "projects" / "demo-n64" / "original" / "game.c"

needs_tools = pytest.mark.skipif(
    any(toolchains.installed(t) is None for t in ("msvc", "objdiff-cli")) or not ghidra.available()
    or (sys.platform != "win32" and toolchains.get("wibo").supported and toolchains.installed("wibo") is None),
    reason="needs Visual C++ (msvc, proprietary), Ghidra and objdiff")


def test_decorated_names():
    assert scratch_pe.decorate("func_00401000", "__cdecl", 0, 2) == "_func_00401000"
    assert scratch_pe.decorate("func_00401000", "__stdcall", 0, 0) == "_func_00401000"  # Ghidra's default: nothing popped
    assert scratch_pe.decorate("func_00401000", "__stdcall", 8, 2) == "_func_00401000@8"
    assert scratch_pe.decorate("func_00401000", "__fastcall", 0, 2) == "@func_00401000@8"
    assert scratch_pe.decorate("func_00401000", "__fastcall", 4, 3) == "@func_00401000@12"
    assert scratch_pe.decorate("func_00401000", "__thiscall", 4, 2) == "@func_00401000@12"  # this, unused edx, one int


def test_generated_pc_builds_quote_paths_and_only_process_successful_objects(tmp_path, monkeypatch):
    """The compiler and its includes may live in a Windows user folder with spaces or shell characters."""
    root = tmp_path / "Project with spaces ü"
    tools = Path(scratch_pe.__file__).with_name("project_template") / "tools"
    shutil.copytree(tools, root / "tools")
    source = root / "src/auto/sample.cpp"
    source.parent.mkdir(parents=True)
    source.write_text("int sample(void) { return 1; }\n")
    config = root / "config/TEST/functions.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"exe": "orig/test.exe", "units": {"sample": {}}}))
    (root / "include").mkdir()
    compiler_root = tmp_path / "Compiler & tools $ ü"
    compiler = compiler_root / "6.0/Bin/CL.EXE"
    compiler.parent.mkdir(parents=True)
    (compiler_root / "6.0/Include").mkdir()
    record = root / "compiler-args.txt"
    monkeypatch.setenv("DECOMP_TEST_CL_ARGS", str(record))
    monkeypatch.delenv("DECOMP_TEST_CL_FAIL", raising=False)
    if sys.platform == "win32":
        subprocess.run([sys.executable, str(Path(__file__).with_name("fake_clr_compiler.py")), str(compiler)],
                       check=True, capture_output=True, timeout=30)
    else:
        compiler.write_text('#!/usr/bin/env python3\nimport os, sys\nfrom pathlib import Path\n'
                            'Path(os.environ["DECOMP_TEST_CL_ARGS"]).write_text("\\n".join(sys.argv[1:]))\n'
                            'if os.environ.get("DECOMP_TEST_CL_FAIL") == "1": sys.exit(3)\n'
                            'out = Path(next(arg[3:] for arg in sys.argv if arg.startswith("/Fo")))\n'
                            'out.parent.mkdir(parents=True, exist_ok=True)\nout.write_text("object")\n')
        compiler.chmod(0o755)
    # Only command routing is under test: no fake object ever reaches the real judge.
    (root / "tools/names.py").write_text('import sys\nfrom pathlib import Path\n'
                                        'out = Path(sys.argv[2])\nassert out.read_text() == "object"\n'
                                        'out.with_suffix(".named").write_text("processed")\n')
    # every compiler pack: the build takes the one with its version (the 2005-2010 pack has no 6.0)
    newer = tmp_path / "newer pack"
    (newer / "10.0SP1" / "Bin").mkdir(parents=True)
    subprocess.run([sys.executable, str(root / "tools/configure.py"), "TEST", "--msvc", str(newer), "--msvc",
                    str(compiler_root)], check=True, capture_output=True, timeout=30)
    project = ForkProject(root)
    ok, output = project.run(["ninja", "all_source"], log=lambda _: None, timeout=30)
    assert ok, output
    obj = root / "build/TEST/src/auto/sample.obj"
    assert obj.with_suffix(".named").read_text() == "processed"
    args = record.read_text(encoding="utf-8-sig").splitlines()
    assert "/I" + str(compiler_root / "6.0/Include") in args and "/GX" in args
    obj.with_suffix(".named").unlink()
    obj.unlink()
    source.write_text("int sample(void) { return 2; }\n")  # also exercise configure.py's regeneration command
    monkeypatch.setenv("DECOMP_TEST_CL_FAIL", "1")
    ok, output = project.run(["ninja", "all_source"], log=lambda _: None, timeout=30)
    assert not ok and not obj.with_suffix(".named").exists(), output
    # a file of its own compiler (file_compilers.json): the build follows the map as it changes
    monkeypatch.delenv("DECOMP_TEST_CL_FAIL")
    shutil.copytree(compiler_root / "6.0", compiler_root / "7.1")
    compilers = config.with_name("file_compilers.json")
    compilers.write_text("{}\n")
    subprocess.run([sys.executable, str(root / "tools/configure.py"), "TEST", "--msvc", str(compiler_root)],
                   check=True, capture_output=True, timeout=30)
    compilers.write_text(json.dumps({"src/auto/sample.cpp": "7.1"}))
    ok, output = project.run(["ninja", "all_source"], log=lambda _: None, timeout=30)
    assert ok, output
    assert "/I" + str(compiler_root / "7.1/Include") in record.read_text(encoding="utf-8-sig").splitlines()
    compilers.write_text(json.dumps({"src/auto/sample.cpp": "9.9"}))
    ok, output = project.run(["ninja", "all_source"], log=lambda _: None, timeout=30)
    assert not ok and "Visual C++ 9.9 isn't in" in output, output


def _template_tool(name: str):
    import importlib.util

    path = Path(scratch_pe.__file__).with_name("project_template") / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"pc_template_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_real_name_stands_for_its_address_however_c_or_cpp_spells_it():
    names = _template_tool("names")
    real = names.real_names({"names": {"func_00401000": "UpdateTown", "D_00405000": "g_town"}})
    for sym in (b"?UpdateTown@@YAXH@Z", b"?UpdateTown@Town@@QAEXH@Z", b"_UpdateTown", b"_UpdateTown@8", b"@UpdateTown@8"):
        assert names.address_of(sym, real) == b"func_00401000", sym
    assert names.address_of(b"?g_town@@3HA", real) == names.address_of(b"_g_town", real) == b"D_00405000"
    assert names.address_of(b"_func_00402000@4", real) == b"func_00402000"  # an address still says it
    for other in (b"?UpdateTownHall@@YAXXZ", b"_memcpy", b"??0Town@@QAE@XZ", b".text"):
        assert names.address_of(other, real) is None, other
    # a method named with its class (a DLL exports start of two classes): Town::start is the class's, not Mill's
    real = names.real_names({"names": {"func_00401000": "Town::start", "func_00402000": "Game::UI::start"}})
    assert names.address_of(b"?start@Town@@QAEXXZ", real) == b"func_00401000"
    assert names.address_of(b"?start@UI@Game@@QAEXXZ", real) == b"func_00402000"
    assert names.address_of(b"?start@Mill@@QAEXXZ", real) is None


def test_a_constructor_or_destructor_stands_for_the_address_its_class_names():
    """A class's constructor and destructor are two functions at two addresses: named after the class (functions.json's
    "names": CFoo::CFoo, CFoo::~CFoo), each pairs with its own, and calls to it too, nested classes as well."""
    names = _template_tool("names")
    real = names.real_names({"names": {"func_00401000": "CFoo::CFoo", "func_00401020": "CFoo::~CFoo",
                                       "func_00401040": "Outer::Inner::Inner"}})
    assert names.address_of(b"??0CFoo@@QAE@XZ", real) == b"func_00401000"
    assert names.address_of(b"??1CFoo@@QAE@XZ", real) == names.address_of(b"??1CFoo@@UAE@XZ", real) == b"func_00401020"
    assert names.address_of(b"??0Inner@Outer@@QAE@XZ", real) == b"func_00401040"
    for other in (b"??0CBar@@QAE@XZ", b"??1Inner@Outer@@QAE@XZ", b"??_GCFoo@@UAEPAXI@Z", b"?CFoo@@YAXXZ"):
        assert names.address_of(other, real) is None, other
    assert names.address_of(b"??1func_004D9DD0@@QAE@XZ", {}) == b"func_004D9DD0"  # a class named after an address
    # in the object: the definition and a call to it from another function get the original's symbol
    obj = _coff([b"??0CFoo@@QAE@XZ", b"??1CFoo@@QAE@XZ", b"_func_00401060"])
    out = names.rename(obj, {b"func_00401000": b"_func_00401000", b"func_00401020": b"_func_00401020@4"}, real)
    assert _coff_names(out) == [b"_func_00401000", b"_func_00401020@4", b"_func_00401060"]


def _coff(symbols: list[bytes]) -> bytes:
    """A COFF object with no sections, only these symbols (long names in the string table)."""
    strings, table = bytearray(4), b""
    for name in symbols:
        table += struct.pack("<II", 0, len(strings)) + struct.pack("<IhHBB", 0, 0, 0x20, 2, 0)
        strings += name + b"\0"
    struct.pack_into("<I", strings, 0, len(strings))
    return struct.pack("<HHIIIHH", 0x14C, 0, 0, 20, len(symbols), 0, 0) + table + bytes(strings)


def _coff_names(data: bytes) -> list[bytes]:
    at, count = struct.unpack_from("<II", data, 8)
    strings = data[at + 18 * count:]
    out = []
    for i in range(count):
        raw = data[at + 18 * i:at + 18 * i + 8]
        off = struct.unpack_from("<I", raw, 4)[0] if raw[:4] == b"\0\0\0\0" else None
        out.append(strings[off:strings.index(b"\0", off)] if off is not None else raw.rstrip(b"\0"))
    return out


def test_a_pc_symbol_is_renamed_by_its_address_in_functions_json(tmp_path):
    from decompnet.forks.project import ForkError

    conf = tmp_path / "config" / "v"
    conf.mkdir(parents=True)
    (conf / "functions.json").write_text(json.dumps(
        {"units": {"a": [{"name": "_func_00401000@8", "addr": 0x401000, "relocs": []}]}}, indent=1))
    fp, pc = ForkProject(tmp_path), consoles.get("pc")
    assert list(pc.rename_symbol(fp, "v", "_func_00401000@8", "UpdateTown")) == [conf / "functions.json"]
    assert pc.real_names(fp, "v") == {"_func_00401000@8": "UpdateTown"}  # by the report's name
    pc.rename_symbol(fp, "v", "UpdateTown", "Town_Update")  # again, by the name it has
    pc.rename_symbol(fp, "v", "D_00405000", "g_town")
    assert json.loads((conf / "functions.json").read_text())["names"] == {"D_00405000": "g_town",
                                                                         "func_00401000": "Town_Update"}
    for old, new in (("func_00409999", "Nowhere"), ("nothing", "Other"), ("D_00405004", "g_town")):
        with pytest.raises(ForkError):
            pc.rename_symbol(fp, "v", old, new)
    # however the source spells it: the report's decorated name, a method, not a longer address
    pattern = pc.rename_pattern("_func_00401000@8")
    assert re.sub(pattern, "T", "void Town::func_00401000(int); _func_00401000(); func_004010001();") == \
        "void Town::T(int); T(); func_004010001();"


def test_two_renames_at_once_both_reach_every_file(tmp_path, monkeypatch):
    """Eight polishing helpers renamed at once: each read the files before taking the project's lock and wrote them
    back after, undoing the renames made meanwhile in some files and not others, and an Xbox game's harness link lost
    six functions. Here both start while something else (a build) holds the project."""
    import threading

    from decompnet.app.engine import RomNetwork
    from decompnet.forks.project import Unit

    net = RomNetwork(tmp_path / "decomp.db", tmp_path / "ws")
    root = tmp_path / "ws" / "p"
    (root / "config" / "v").mkdir(parents=True)
    (root / "config" / "v" / "functions.json").write_text(json.dumps({"units": {"u": [
        {"name": "_func_00401000", "addr": 0x401000, "relocs": []},
        {"name": "_func_00401010", "addr": 0x401010, "relocs": []}]}}, indent=1))
    (root / "src" / "auto").mkdir(parents=True)
    source = root / "src" / "auto" / "u.cpp"
    source.write_text("int func_00401000(int x) { return x; }\nint func_00401010(int x) { return func_00401000(x); }\n")
    (root / "objdiff.json").write_text(json.dumps({"units": []}))
    net.forks._register("p", "P", "fork", root, {"kind": "fork", "origin": "scratch", "version": "v",
                                                  "platform": "pc", "ready": True}, "x")
    report = {"units": [{"name": "u", "functions": [{"name": n, "size": "8", "fuzzy_match_percent": 100.0}
                                                    for n in ("_func_00401000", "_func_00401010")]}]}
    monkeypatch.setattr(ForkProject, "report", lambda self: report)
    monkeypatch.setattr(ForkProject, "units", lambda self: [Unit("u", None, None, "src/auto/u.cpp", False, None)])
    monkeypatch.setattr(ForkProject, "setup_commands", lambda self, version: [])
    monkeypatch.setattr(ForkProject, "git", lambda self, *args: "")
    net.forks.sync("p", log=lambda *_: None)
    agent, _ = net.local_agent("claude-1")
    said = {}
    first = threading.Thread(target=lambda: said.update(a=net.rename_symbol(agent, "p", "_func_00401000", "Identity")))
    second = threading.Thread(target=lambda: said.update(b=net.rename_symbol(agent, "p", "_func_00401010", "Pass")))
    with net.forks._tree(net.project_row("p")).exclusive():  # a build holds the project while both start
        first.start()
        second.start()
        first.join(0.5)  # (they wait for it now; before, each read the files here)
    first.join(10)
    second.join(10)
    assert said["a"].startswith("✅") and said["b"].startswith("✅"), said
    assert source.read_text() == "int Identity(int x) { return x; }\nint Pass(int x) { return Identity(x); }\n"
    names = json.loads((root / "config" / "v" / "functions.json").read_text())["names"]
    assert names == {"func_00401000": "Identity", "func_00401010": "Pass"}


def test_an_agents_constructor_and_destructor_name_the_functions_they_are(tmp_path, monkeypatch):
    """A Visual C++ game's small exception-using functions are often constructors and destructors, and a class's two
    are at two addresses: named after one address (func_004A8330::func_004A8330), only that one paired. Written as
    CFoo::CFoo for one function and CFoo::~CFoo for the other, each attempt gives its function that real name (here a
    stand-in build pairs a function by it, as tools/names.py does), committed with the match; an attempt undone takes
    its name back, and a name another function has is refused."""
    from decompnet.app.engine import RomNetwork
    from decompnet.forks import objdiff
    from decompnet.forks.project import Unit
    from decompnet.network import NetworkError

    net = RomNetwork(tmp_path / "decomp.db", tmp_path / "ws")
    root = tmp_path / "ws" / "p"
    config = root / "config" / "v" / "functions.json"
    config.parent.mkdir(parents=True)
    functions = ["_func_00401000", "_func_00401020@4", "_func_00401040", "_func_00401060"]
    config.write_text(json.dumps({"units": {"u": [{"name": n, "addr": int(n[6:14], 16), "relocs": []}
                                                  for n in functions]}}, indent=1))
    (root / "src" / "auto").mkdir(parents=True)
    source = root / "src" / "auto" / "u.cpp"
    source.write_text("struct CFoo { CFoo(); ~CFoo(); int x; };\n"  # (edit_file puts classes in, before)
                      "struct CBar { CBar(); int x; };\nstruct CBaz { CBaz(); ~CBaz(); };\n")
    (root / "objdiff.json").write_text(json.dumps({"units": []}))
    net.forks._register("p", "P", "fork", root, {"kind": "fork", "origin": "scratch", "version": "v",
                                                  "platform": "pc", "ready": True}, "x")
    report = {"units": [{"name": "u", "functions": [{"name": n, "size": "8"} for n in functions]}]}
    unit = Unit("u", None, None, "src/auto/u.cpp", False, None)
    committed = []
    monkeypatch.setattr(ForkProject, "report", lambda self: report)
    monkeypatch.setattr(ForkProject, "units", lambda self: [unit])
    monkeypatch.setattr(ForkProject, "unit", lambda self, name: unit)
    monkeypatch.setattr(ForkProject, "linkable", lambda self: False)
    monkeypatch.setattr(ForkProject, "build_problem", lambda self: None)
    monkeypatch.setattr(ForkProject, "build_unit", lambda self, u, log=None: ("CBar::" not in source.read_text(), "C2059"))
    monkeypatch.setattr(ForkProject, "git", lambda self, *args: committed.append(args) or "")

    def diff(self, u, symbol):
        """What tools/names.py pairs: CFoo::~CFoo's code with the function functions.json names so."""
        given = json.loads(config.read_text()).get("names", {})
        scores = {n: 100.0 if (real := given.get(f"func_{n[6:14]}")) and csource.find(source.read_text(), real) else 50.0
                  for n in functions}
        return objdiff.FunctionDiff(symbol, scores[symbol], [], 8, scores, [], {})

    monkeypatch.setattr(ForkProject, "diff", diff)
    net.forks.sync("p", log=lambda *_: None)
    agent, _ = net.local_agent("claude-1")
    res = net.attempt(agent, "_func_00401000", "CFoo::CFoo() {\n    x = 0;\n}\n", "p")
    assert res["matched"], res["text"]
    assert ("add", "--", "src/auto/u.cpp", "config/v/functions.json") in committed  # the name goes with the match
    res = net.attempt(agent, "_func_00401020@4", "CFoo::~CFoo() {\n    x = 1;\n}\n", "p")
    assert res["matched"], res["text"]
    assert json.loads(config.read_text())["names"] == {"func_00401000": "CFoo::CFoo", "func_00401020": "CFoo::~CFoo"}
    text = source.read_text()
    assert "CFoo::CFoo() {" in text and "CFoo::~CFoo() {" in text
    # again, as the function is named already: the same definition, no new name
    assert net.attempt(agent, "_func_00401020@4", "CFoo::~CFoo() {\n    x = 1; \n}\n", "p")["matched"]
    with pytest.raises(NetworkError, match="names another"):  # another function can't be CFoo's constructor
        net.attempt(agent, "_func_00401040", "CFoo::CFoo() {\n    x = 2;\n}\n", "p")
    res = net.attempt(agent, "_func_00401040", "CBar::CBar() {\n    x = 3;\n}\n", "p")
    assert "BUILD ERROR" in res["text"] and "func_00401040" not in json.loads(config.read_text())["names"]
    assert "CBar::CBar" not in source.read_text()
    # (two at once: which is the function?)
    with pytest.raises(NetworkError, match="complete definition"):
        net.attempt(agent, "_func_00401060", "CBaz::CBaz() {\n}\nCBaz::~CBaz() {\n}\n", "p")


def test_only_32_bit_x86_programs():
    assert not scratch_pe.is_pe(b"MZ" + bytes(0x3E))
    head = bytearray(0x200)
    head[:2], head[0x3C:0x40], head[0x80:0x84] = b"MZ", struct.pack("<I", 0x80), b"PE\0\0"
    struct.pack_into("<H", head, 0x84, 0x14C)
    struct.pack_into("<H", head, 0x98, 0x10B)
    assert scratch_pe.is_pe(bytes(head))
    struct.pack_into("<H", head, 0x84, 0x8664)  # x64
    assert not scratch_pe.is_pe(bytes(head))


def test_a_function_your_object_only_calls_is_not_in_your_build_not_a_match(monkeypatch):
    import json

    from decompnet.forks import objdiff

    # an object that calls F but doesn't define it (a unit whose definition went missing): objdiff lists the call's
    # undefined symbol under the function's name, with no size or code. The function's diff read 100% (its code
    # against nothing), the full report said not built, and every full measure reopened a function the next
    # compile "matched" again (63 times in 14 hours in one project)
    ins = [{"instruction": {"formatted": "mov eax, 1", "size": 5}}, {"instruction": {"formatted": "ret", "size": 1}}]
    left = {"symbols": [{"name": "?F@@YA_NXZ", "kind": "SYMBOL_FUNCTION", "size": "6", "instructions": ins}]}
    right = {"symbols": [{"name": "?F@@YA_NXZ", "kind": "SYMBOL_FUNCTION", "flags": {"global": True}},
                         {"name": "?G@@YAXXZ", "kind": "SYMBOL_FUNCTION", "size": "6", "instructions": ins}]}
    monkeypatch.setattr(objdiff, "_run", lambda *a, **k: json.dumps({"left": left, "right": right}))
    d = objdiff.diff(None, None, "?F@@YA_NXZ", cwd=Path("."))
    assert d.match_percent is None and not d.matched
    assert d.unit_scores["?F@@YA_NXZ"] is None
    # defined after its undefined copy: that's the one compared
    right["symbols"].append({"name": "?F@@YA_NXZ", "kind": "SYMBOL_FUNCTION", "size": "6", "match_percent": 100.0,
                             "instructions": ins})
    d = objdiff.diff(None, None, "?F@@YA_NXZ", cwd=Path("."))
    assert d.match_percent == 100.0 and d.matched


def _map(path: Path = MAP) -> dict[str, int]:
    """Symbol -> address of every function and variable, from the linker's map of the demo."""
    return {m.group(1): int(m.group(2), 16)
            for m in re.finditer(r"^\s+\S+\s+(\S+)\s+([0-9a-f]{8}) ", path.read_text(), re.M)}


def _addresses(path: Path = MAP) -> dict[str, str]:
    """The demo's C names -> the project's (func_<address>, D_<address>), from a build's map."""
    return {re.sub(r"^[_@]|@\d+$", "", s): (f"func_{a:08X}" if a < 0x402000 else f"D_{a:08X}")
            for s, a in _map(path).items() if not s.startswith("??") and s != "__fltused"}


def _by_address(path: Path = MAP):
    """The demo's C, written with the project's names."""
    rename = _addresses(path)
    pattern = re.compile(r"\b(" + "|".join(sorted(rename, key=len, reverse=True)) + r")\b")
    return lambda text: pattern.sub(lambda m: rename[m.group(1)], text)


@pytest.fixture(scope="module")
def pc(tmp_path_factory):
    from decompnet.app.engine import RomNetwork

    if not MAP.exists():  # (a build from before the map was symbols.map: its demoquest.map would name everything)
        subprocess.run([sys.executable, str(DEMO / "build.py")], check=True)
    work = tmp_path_factory.mktemp("pc")
    net = RomNetwork(work / "app.db", work)
    slug = net.import_rom(EXE, confirmed=True, log=lambda *_: None)
    return net, slug


@needs_tools
def test_every_function_is_found_with_its_extent(pc):
    net, slug = pc
    ov = net.overview(slug)
    assert ov["kind"] == "fork" and ov["origin"] == "scratch" and ov["platform"] == "pc" and ov["ready"]
    assert ov["rebuild"]["identical"]  # its code, put back together from the delinked functions, is the program's
    syms = {a: s for s, a in _map().items() if not s.startswith("??") and a < 0x402000}
    rows = net.store.all("SELECT name, size FROM functions WHERE project_id=?", net.project_row(slug)["id"])
    assert len(rows) == len(syms) == 19  # clamp_s32, which nothing calls, too: Ghidra misses it, the gap doesn't
    for name, size in ((r["name"], r["size"]) for r in rows):
        addr = int(re.search(r"func_([0-9A-F]{8})", name).group(1), 16)
        assert addr in syms
        assert name.startswith("@") == syms[addr].startswith("@") and name.endswith("@8") == syms[addr].endswith("@8")
    root = Path(net.project_row(slug)["root"])
    files = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True).stdout.split()
    assert files and not [f for f in files if f.startswith(("orig/", "build/"))]
    assert "config/demoquest/functions.json" in files and any(f.startswith("src/auto/") and f.endswith(".cpp") for f in files)


@needs_tools
def test_agents_match_functions(pc):
    net, slug = pc
    syms = _map()
    agent, _ = net.local_agent("pc-agent")
    header = (GAME.parent / "game.h").read_text().split("#define NULL ((void *)0)", 1)[1].rsplit("#endif", 1)[0]
    rename = _addresses()
    sub = _by_address()
    header += "extern s32 g_settings[6];\n"
    assert net.forks.edit_file(agent, slug, "include/common.h", "", sub(header), "the game's types").startswith("✅")
    source = GAME.read_text() + (DEMO / "pcmain.c").read_text()
    for name in ("clamp_s32", "player_take_damage", "entity_default_hp", "settings_get", "mix_score"):
        decorated = next(s for s in syms if re.sub(r"^[_@]|@\d+$", "", s) == name)
        res = net.attempt(agent, decorated.replace(name, rename[name]), sub(csource.get(source, name)), slug)
        assert res["matched"], res["text"]
    # C++: a class's method, whatever MSVC mangles its name to, pairs with the original's function
    abs_name = rename["abs_s32"]
    assert net.forks.edit_file(agent, slug, "include/common.h", "", f"struct Num {{ static s32 {abs_name}(s32 v); }};\n",
                               "a class").startswith("✅")
    res = net.attempt(agent, f"_{abs_name}", f"s32 Num::{abs_name}(s32 v) {{\n    return v < 0 ? -v : v;\n}}\n", slug)
    assert res["matched"], res["text"]
    # polishing: the comment saying what a function does goes into its file with it (an Xbox game's 18 polished
    # functions kept their definitions only), a new one replaces it, and a rewrite without one leaves it
    root = Path(net.project_row(slug)["root"])
    clamp = next(s for s in syms if re.sub(r"^[_@]|@\d+$", "", s) == "clamp_s32").replace("clamp_s32", rename["clamp_s32"])
    code = sub(csource.get(source, "clamp_s32")).strip() + "\n"
    file = next(p for p in (root / "src").rglob("*.c*") if csource.find(p.read_text(), rename["clamp_s32"]))

    def polish(comment: str) -> str:
        res = net.readability.submit(agent, slug, clamp, comment + code)
        assert "readable now" in res, res
        return file.read_text()

    text = polish("// Keeps v within lo and hi.\n")
    assert "// Keeps v within lo and hi.\n" + code.splitlines()[0] in text  # right above the definition
    text = polish("/* Clamps v to [lo, hi],\n   both ends included. */\n")
    assert "Keeps v within" not in text and text.count("both ends included. */\n" + code.splitlines()[0]) == 1
    code = code.replace("{", "{ /* the bounds are included */", 1)  # (other C than it has: a rewrite)
    text = polish("")
    assert "both ends included. */\n" + code.splitlines()[0] in text  # no comment sent: the file's stays
    # only polishing's comments go in: a twin's note or an agent's scratch comment replaced a forked decomp's own
    res = net.attempt(agent, clamp, "// scratch\n" + code.replace("the bounds", "bounds"), slug)
    assert res["matched"], res["text"]
    text = file.read_text()
    assert "scratch" not in text and "both ends included. */\n" in text
    # real names: a __stdcall function and the global it reads renamed, both still match (an Xbox game's polishing
    # helpers were refused 31 renames); its row keeps the report's name and finds its code by the new one
    getter = next(s for s in syms if re.sub(r"^[_@]|@\d+$", "", s) == "settings_get")
    getter = getter.replace("settings_get", rename["settings_get"])
    assert net.rename_symbol(agent, slug, rename["g_settings"], "g_settings").startswith("✅")
    assert net.rename_symbol(agent, slug, getter, "settings_get").startswith("✅")
    texts = "".join(p.read_text() for p in (root / "src").rglob("*.c*")) + (root / "include" / "common.h").read_text()
    assert "settings_get(" in texts and rename["settings_get"] not in texts and rename["g_settings"] not in texts
    fn = net._function(getter, slug)
    assert fn["status"] == "matched" and json.loads(fn["meta"])["real"] == "settings_get"
    assert net._function("settings_get", slug)["id"] == fn["id"]
    # a renamed global is read by its new name (a helper that named one could no longer read_data it)
    assert net.forks.read_data(agent, slug, "g_settings", 24).startswith(f"g_settings = {rename['g_settings']} (")
    again = sub(csource.get(source, "settings_get")).replace(rename["settings_get"], "settings_get")
    res = net.attempt(agent, "settings_get", again.replace(rename["g_settings"], "g_settings"), slug)
    assert res["matched"], res["text"]
    other = f"_func_{syms['_entity_points']:08X}"
    wrong = net.attempt(agent, other, sub(csource.get(source, "entity_points")).replace("return 75;", "return 76;"),
                        slug)
    assert not wrong["matched"]
    net.release(agent, other, "", slug)
    root = Path(net.project_row(slug)["root"])
    assert "Match " in subprocess.run(["git", "log", "--oneline"], cwd=root, capture_output=True, text=True).stdout


@needs_tools
def test_the_project_rebuilds_the_whole_program_from_its_matched_c(pc, tmp_path):
    """The rebuildable export on PC is the project itself: `ninja rebuild` puts the whole .exe back together with
    every matched function from its C, the rebuild check says it's the user's copy byte for byte, and a fresh clone of
    the project, given the user's copy, builds the same."""
    net, slug = pc
    proj = net.project_row(slug)
    root, version = Path(proj["root"]), "demoquest"
    agent, _ = net.local_agent("pc-agent")
    clamp = next(s for s in _map() if re.sub(r"^[_@]|@\d+$", "", s) == "clamp_s32")
    res = net.attempt(agent, clamp.replace("clamp_s32", _addresses()["clamp_s32"]),
                      _by_address()(csource.get(GAME.read_text(), "clamp_s32")), slug)
    assert res["matched"], res["text"]
    matched = {r["name"] for r in net.store.all("SELECT name FROM functions WHERE project_id=? AND status='matched'",
                                                proj["id"])}
    result = net.forks.check_rom(slug, log=lambda *_: None)
    assert result["identical"] and result["what"] == "The game's program, rebuilt from the project,", result
    from_c = set(json.loads((root / "build" / version / "rebuild.json").read_text())["c"])
    assert matched and matched <= from_c, matched - from_c
    assert net.overview(slug)["rebuild"]["identical"]
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(root), str(clone)], check=True)
    (clone / "orig").mkdir()
    shutil.copyfile(root / "orig" / f"{version}.exe", clone / "orig" / f"{version}.exe")
    fp = ForkProject(clone)
    for cmd in ([sys.executable, "tools/configure.py", version, *scratch_pe.tool_args()], ["ninja", "rebuild"]):
        ok, out = fp.run(cmd, log=lambda *_: None)
        assert ok, out
    assert (clone / "build" / version / f"{version}.exe").read_bytes() == (root / "orig" / f"{version}.exe").read_bytes()
    assert set(json.loads((clone / "build" / version / "rebuild.json").read_text())["c"]) == from_c


NEWER = ["8.0SP1", "9.0", "9.0SP1", "10.0", "10.0SP1"]  # the msvc2005 pack


@pytest.mark.parametrize("version", NEWER)
@needs_tools
@pytest.mark.skipif(toolchains.installed("msvc2005") is None,
                    reason="needs Visual C++ 2005, 2008 and 2010 (msvc2005, proprietary)")
def test_a_game_of_2005_to_2010_builds_with_its_own_visual_cpp_and_matches(tmp_path, version):
    """Newer Windows games, phase C: Demo Quest built by each Visual C++ of 2005-2010 imports with that very build
    (its Rich header names it), the project compiles with it (/EHsc, its name for /GX), and agents' C matches."""
    from decompnet.app.engine import RomNetwork

    build = DEMO / "build" / version
    subprocess.run([sys.executable, str(DEMO / "build.py"), version], check=True, capture_output=True)
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    said = []
    slug = net.import_rom(build / "demoquest.exe", confirmed=True, log=said.append)
    assert net.overview(slug)["rebuild"]["identical"]
    assert any(f": Visual C++ {version}" in line and "closest" not in line for line in said), said
    configure = (Path(net.project_row(slug)["root"]) / "tools" / "configure.py").read_text()
    assert f'MSVC_VERSION = "{version}"' in configure and '"/EHsc"]' in configure
    syms = _map(build / "symbols.map")
    agent, _ = net.local_agent("pc-agent")
    sub = _by_address(build / "symbols.map")
    header = (GAME.parent / "game.h").read_text().split("#define NULL ((void *)0)", 1)[1].rsplit("#endif", 1)[0]
    header += "extern s32 g_settings[6];\n"
    assert net.forks.edit_file(agent, slug, "include/common.h", "", sub(header), "the game's types").startswith("✅")
    source = GAME.read_text() + (DEMO / "pcmain.c").read_text()
    for name in ("clamp_s32", "entity_default_hp", "settings_get", "mix_score", "entity_points"):
        # by its address: inlined where they're called, some are found in the gaps between functions, without the
        # calling convention a call shows
        address = next(a for s, a in syms.items() if re.sub(r"^[_@]|@\d+$", "", s) == name)
        row = net.store.one("SELECT name FROM functions WHERE project_id=? AND name LIKE ?",
                            net.project_row(slug)["id"], f"%func_{address:08X}%")
        res = net.attempt(agent, row["name"], sub(csource.get(source, name)), slug)
        assert res["matched"], (name, res["text"])


@pytest.mark.parametrize(("built", "other"), [("10.0SP1", "10.0"), ("8.0", "8.0")])
@needs_tools
@pytest.mark.skipif(toolchains.installed("msvc2005") is None,
                    reason="needs Visual C++ 2005, 2008 and 2010 (msvc2005, proprietary)")
def test_a_file_of_a_newer_game_takes_any_installed_packs_visual_cpp(tmp_path, built, other):
    """A compiler per source file in a newer game: the map can name a version of either pack (2010 RTM from the
    2005-2010 pack, 7.1 from the other), and the build finds each in the pack that has it. 2005 RTM (#42) shares SP1's
    Rich build, so a game of it imports with SP1 and its files are switched to RTM this way."""
    from decompnet.app.engine import RomNetwork

    build = DEMO / "build" / built
    subprocess.run([sys.executable, str(DEMO / "build.py"), built], check=True, capture_output=True)
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    slug = net.import_rom(build / "demoquest.exe", confirmed=True, log=lambda *_: None)
    agent, _ = net.local_agent("pc-agent")
    root = Path(net.project_row(slug)["root"])
    syms, sub = _map(build / "symbols.map"), _by_address(build / "symbols.map")
    header = (GAME.parent / "game.h").read_text().split("#define NULL ((void *)0)", 1)[1].rsplit("#endif", 1)[0]
    assert net.forks.edit_file(agent, slug, "include/common.h", "", sub(header + "extern s32 g_settings[6];\n"),
                               "the game's types").startswith("✅")
    named = lambda symbol: net.store.one("SELECT name FROM functions WHERE project_id=? AND name LIKE ?",
                                         net.project_row(slug)["id"], f"%func_{syms[symbol]:08X}%")["name"]
    adder = named("_add_score")
    whole = max(p.relative_to(root).as_posix() for p in (root / "src" / "auto").glob("*.cpp")
                if p.stem <= f"text_{syms['_add_score']:08X}")
    assert net.forks.split_file(agent, slug, whole, adder).startswith("✅")
    last = f"src/auto/text_{syms['_add_score']:08X}.cpp"
    said = net.forks.try_compiler(agent, slug, adder, f"MSVC/{other}", source=sub(csource.get(GAME.read_text(), "add_score")))
    assert f"{last} is built with MSVC/{other} from now on" in said, said
    assert scratch_pe.file_compilers(root, "demoquest") == {last: other}
    if built == "10.0SP1":  # 7.1, from the other pack, builds the file too, and compiles add_score otherwise (2005
        # RTM and 7.1 compile it alike): the file keeps its compiler
        with pytest.raises(NetworkError, match=f"stays with MSVC/{other}: .* no longer match"):
            net.forks.set_file_compiler_by_hand(slug, last, "MSVC/7.1")
        assert scratch_pe.file_compilers(root, "demoquest") == {last: other}
    assert net._function(adder, slug)["status"] == "matched"


@needs_tools
def test_agents_divide_a_file_and_give_it_its_own_compiler(pc):
    """split_file on PC divides a source file (the rebuild still identical, every match holding), and each file can
    have its own Visual C++: chosen in Settings only when its matches hold (a file whose matched function Visual C++
    7.1 compiles otherwise stays with 6.0), switched by try_compiler when a function matches with another one. The
    judge, real names and Check again work on the files it made (AGENTS.md: every feature on the console)."""
    net, slug = pc
    agent, _ = net.local_agent("pc-agent")
    root = Path(net.project_row(slug)["root"])
    syms = _map()
    assert net.forks.split_file(agent, slug, "src/auto/text_00401000.cpp",
                                f"_func_{syms['_clamp_s32']:08X}").startswith("✅ src/auto/text_00401000.cpp is divided")
    assert net.overview(slug)["rebuild"]["identical"]
    second = f"src/auto/text_{syms['_clamp_s32']:08X}.cpp"
    with pytest.raises(NetworkError, match="stays with MSVC/6.0: .* no longer match"):  # player_take_damage, in it
        net.forks.set_file_compiler_by_hand(slug, second, "MSVC/7.1")
    assert net.forks.split_file(agent, slug, second, f"_func_{syms['_entity_spawn']:08X}").startswith("✅")
    last = f"src/auto/text_{syms['_entity_spawn']:08X}.cpp"
    net.forks.set_file_compiler_by_hand(slug, last, "MSVC/7.1")  # (nothing in it matches yet)
    assert scratch_pe.file_compilers(root, "demoquest") == {last: "7.1"}
    assert "each source file can have its own compiler" in net.forks.info(slug)
    adder = f"_func_{syms['_add_score']:08X}"
    sub = _by_address()
    said = net.forks.try_compiler(agent, slug, adder, "MSVC/6.0", source=sub(csource.get(GAME.read_text(), "add_score")))
    assert f"{last} is built with MSVC/6.0 from now on" in said, said
    assert scratch_pe.file_compilers(root, "demoquest") == {} and net._function(adder, slug)["status"] == "matched"
    log = subprocess.run(["git", "log", "--format=%s"], cwd=root, capture_output=True, text=True).stdout
    assert f"Build {last} with MSVC/6.0" in log and f"Split text_{syms['_clamp_s32']:08X}.cpp" in log
    # its function, in the file split off, takes a real name and still matches; Check again has nothing to bring
    assert net.rename_symbol(agent, slug, adder, "add_score").startswith("✅")
    assert net._function("add_score", slug)["status"] == "matched" and "add_score(" in (root / last).read_text()
    assert net.recheck(slug, log=lambda *_: None) == "nothing new"


def _windows_program(machine=0x14C, rich=(), sections=(".text", ".rdata", ".data"), linker=(6, 0), stub=0x40):
    """A Windows program's headers, made by hand: the DOS header and `stub` bytes of DOS stub, a Rich header of
    (product id, build, count) entries XORed with its key, the PE header with the linker's version, the sections."""
    key = bytes([0x5A, 0x17, 0x3C, 0x91])
    head = bytearray(b"MZ" + bytes(0x3E + stub))
    if rich:
        plain = b"DanS" + bytes(12) + b"".join(struct.pack("<II", p << 16 | b, n) for p, b, n in rich)
        head += bytes(byte ^ key[i % 4] for i, byte in enumerate(plain)) + b"Rich" + key
    head += bytes(-len(head) % 8)
    struct.pack_into("<I", head, 0x3C, len(head))
    opt = bytearray(0xE0 if machine == 0x14C else 0xF0)
    struct.pack_into("<HBB", opt, 0, 0x10B if machine == 0x14C else 0x20B, *linker)
    head += b"PE\0\0" + struct.pack("<HHIIIHH", machine, len(sections), 0, 0, 0, len(opt), 0x102) + opt
    return bytes(head + b"".join(name.encode().ljust(8, b"\0") + bytes(32) for name in sections))


def test_the_import_names_the_visual_cpp_build_from_the_rich_header():
    """GitHub #12: a 2010 game said only "Ghidra found no functions". The Rich header says which Visual C++ and which
    update built it, and whether decompdecomp can build it too."""
    vc6 = scratch_pe.describe(_windows_program(rich=[(0x0A, 8047, 169), (0x0B, 8966, 148), (0x0B, 8447, 38),
                                                     (0x13, 8034, 23)]))
    # the C library's files come prebuilt by an older build: the game's is the newest that built a tenth of them
    assert (vc6.version, vc6.build, vc6.compiler) == ("6.0", 8966, "Visual C++ 6.0 SP5 (build 8966)")
    assert vc6.blockers() == vc6.cannot_match() == [] and vc6.pack_version == "6.0"
    vs2010 = scratch_pe.describe(_windows_program(rich=[(0xAB, 40219, 503), (0xAA, 40219, 171), (0x30, 9044, 121),
                                                        (0x9D, 40219, 1)]))
    assert vs2010.compiler == "Visual C++ 2010 SP1 (10.0, build 40219)" and vs2010.blockers() == []
    assert (vs2010.pack_version, vs2010.exact, vs2010.cannot_match()) == ("10.0SP1", True, [])
    # each update generates other code: the very build, else the closest of its version, said as such
    vs2008 = scratch_pe.describe(_windows_program(rich=[(0x83, 21022, 40), (0x84, 21022, 300), (0x91, 21022, 1)]))
    assert (vs2008.pack_version, vs2008.exact) == ("9.0", True)
    hotfix = scratch_pe.describe(_windows_program(rich=[(0x84, 30411, 300), (0x91, 30411, 1)]))
    assert (hotfix.pack_version, hotfix.exact) == ("9.0SP1", False)
    # 2005's RTM and SP1 are both build 50727: SP1 builds it
    assert scratch_pe.describe(_windows_program(rich=[(0x6E, 50727, 120)])).pack_version == "8.0SP1"
    vs2012 = scratch_pe.describe(_windows_program(rich=[(0xCF, 60610, 300), (0xCC, 60610, 1)]))
    assert vs2012.pack_version == "10.0SP1" and "2012 to 2015 come next" in vs2012.cannot_match()[0]
    # a 2019 program with middleware built by 2015: the build that linked it compiled the game's own code
    vs2019 = scratch_pe.describe(_windows_program(rich=[(0x109, 30137, 658), (0x104, 24234, 556), (0x105, 24215, 395),
                                                        (0x102, 30137, 1)]))
    assert vs2019.compiler == "Visual C++ 2019 (14.2, build 30137)" and vs2019.out_of_scope
    assert "2017 and newer are out of its scope" in " ".join(vs2019.blockers())
    vs2015 = scratch_pe.describe(_windows_program(rich=[(0x105, 24210, 694), (0x105, 23506, 2095), (0x102, 24210, 1)]))
    assert vs2015.compiler == "Visual C++ 2015 Update 3 (14.0, build 24210)" and not vs2015.out_of_scope
    moved = scratch_pe.describe(_windows_program(rich=[(0x0B, 8168, 10)], stub=0x80))  # a longer DOS stub
    assert (moved.version, moved.build) == ("6.0", 8168)


def test_without_a_rich_header_the_linker_version_names_the_toolset():
    """A store's signature can take the Rich header's place (a 2008 game's has none): the linker's version still
    says which Visual C++ it was."""
    def built(linker):
        return scratch_pe.describe(_windows_program(linker=linker))

    assert built((8, 0)).compiler == "Visual C++ 2005 (8.0, by its linker's version)"
    assert built((8, 0)).build is None and built((8, 0)).pack_version == "8.0SP1"
    assert built((9, 0)).pack_version == "9.0SP1"  # the newest build of its version: the most games had it
    assert (built((7, 10)).version, built((7, 0)).version, built((10, 10)).version) == ("7.1", "7.0", "10.0")
    assert built((7, 10)).cannot_match() == [] and built((14, 29)).out_of_scope
    unknown = built((2, 50))  # not a Microsoft linker's version
    assert unknown.version is None and unknown.pack_version == "6.0" and unknown.blockers() == []


def test_every_visual_cpp_the_import_picks_is_in_a_compiler_pack():
    """RICH_BUILDS names the folders the packs install (toolchains.toml `into`), one for each compiler the console
    lists, so a game's build always finds its compiler where the project looks."""
    from decompnet.consoles.pc import MSVC_VERSIONS

    picked = scratch_pe.RICH_BUILDS.values()
    # every one but 2005 RTM, whose Rich build is SP1's: it's chosen by file (try_compiler, Settings)
    assert sorted([*picked, "8.0"]) == sorted(v.removeprefix("MSVC/") for v in MSVC_VERSIONS)
    assert {scratch_pe.compiler_pack(v) for v in picked} == set(scratch_pe.MSVC_PACKS)
    with pytest.raises(ForkError, match="no Visual C\\+\\+ 5.0"):
        scratch_pe.compiler_pack("5.0")


def test_a_game_whose_visual_cpp_is_not_installed_says_which_pack_to_install(tmp_path, monkeypatch):
    """A 2008 game with only 6.0-7.1 installed: the import names the pack its compiler comes in, before Ghidra runs,
    instead of building with a compiler nothing can match with."""
    monkeypatch.setenv("DECOMPNET_TOOLCHAINS", str(tmp_path / "toolchains"))
    (tmp_path / "toolchains" / "msvc" / "7.1").mkdir(parents=True)
    (tmp_path / "toolchains" / "msvc" / toolchains.MANIFEST).write_text("{}")
    game = tmp_path / "game.exe"
    game.write_bytes(_windows_program(rich=[(0x84, 30729, 300), (0x91, 30729, 1)], linker=(9, 0)))
    with pytest.raises(ForkError, match="Visual C\\+\\+ 9.0SP1, which comes in Microsoft Visual C\\+\\+ 2005, 2008"):
        scratch_pe.create(tmp_path / "project", game, log=lambda *_: None)
    assert not (tmp_path / "project").exists()
    # every installed pack goes to the build, 6.0-7.1's last: an older project's configure.py keeps only the last
    (tmp_path / "toolchains" / "msvc2005").mkdir()
    (tmp_path / "toolchains" / "msvc2005" / toolchains.MANIFEST).write_text("{}")
    monkeypatch.setattr(toolchains, "get", lambda tid: type("T", (), {"supported": False})() if tid == "wibo"
                        else toolchains.registry()[tid])
    assert scratch_pe.tool_args() == ["--msvc", str(tmp_path / "toolchains" / "msvc2005"),
                                      "--msvc", str(tmp_path / "toolchains" / "msvc")]


def test_whole_program_optimisation_is_named_and_a_mostly_ltcg_game_refused():
    mostly = scratch_pe.describe(_windows_program(rich=[(0x60, 3077, 100), (0x64, 3077, 300)]))
    assert (mostly.whole_program, mostly.compiled) == (300, 400)
    assert "300 of its 400 compiled files" in mostly.blockers()[0] and "/LTCG" in mostly.blockers()[0]
    some = scratch_pe.describe(_windows_program(rich=[(0x60, 3077, 300), (0x64, 3077, 10)]))
    assert some.blockers() == [] and some.cannot_match() == [
        "10 of its 310 compiled files were built with whole-program optimisation (/GL): their functions can't match"]
    assert "10 of its 310 compiled files with whole-program optimisation" in some.summary()


def test_a_64_bit_program_is_the_pcs_and_told_why_it_cant_start(tmp_path):
    """The drop refused every 64-bit .exe with "decompdecomp starts PC games from their 32-bit x86 .exe"."""
    from decompnet.forks.project import ForkError

    game = tmp_path / "game.exe"
    game.write_bytes(_windows_program(machine=0x8664, rich=[(0xCF, 60610, 1204), (0xCC, 50727, 1)]))
    assert consoles.identify(game).id == "pc"
    from decompnet.app.engine import RomNetwork

    net = RomNetwork(tmp_path / "app.db", tmp_path / "work")
    with pytest.raises(ForkError) as e:  # the import, before it installs or runs anything
        net.import_rom(game, confirmed=True, log=lambda *_: None)
    assert "64-bit (x64) program" in str(e.value) and "Newer Windows games" in str(e.value)
    assert not list((tmp_path / "work").glob("game*"))
    assert "Visual C++ 2012 Update 3 (11.0, build 60610)" in scratch_pe.describe(game.read_bytes()).summary()
    game.write_bytes(_windows_program(rich=[(0x0B, 8966, 148)]))
    info = scratch_pe.game_info(game)
    assert info["compiler"] == "6.0" and info["cannot_match"] == []
    assert info["summary"] == ("32-bit x86, built with Visual C++ 6.0 SP5 (build 8966), no whole-program "
                               "optimisation, no DRM or packer found")


def test_drm_and_packers_are_named_never_unpacked():
    def wrapper(*sections):
        return scratch_pe.describe(_windows_program(sections=(".text", ".rdata", ".data", *sections)))

    steam = wrapper(".bind")
    assert steam.wrapper == "SteamStub (Steam's DRM)" and "never removes or gets around" in steam.blockers()[0]
    assert wrapper("stxt774", "stxt371").wrapper == "SafeDisc" and wrapper(".securom").wrapper == "SecuROM"
    upx = scratch_pe.describe(_windows_program(sections=("UPX0", "UPX1", ".rsrc")))
    assert upx.packed and "compressed with UPX" in upx.blockers()[0]
    denuvo = wrapper(".xtext", ".xpdata", ".srdata", ".tls", ".pdata", ".reloc", ".rsrc", ".idata")
    assert denuvo.wrapper == "Denuvo"
    # a 2002 game's thirteen sections, its exports' .edata among them, are no wrapper's
    plain = scratch_pe.describe(_windows_program(sections=(".text", "_rwcseg", ".rdata", ".exc", ".rsrc", ".data",
                                                           ".CRT", ".idata", ".tls", "_rwdseg", ".bss", ".edata",
                                                           ".reloc")))
    assert plain.wrapper is None and plain.blockers() == []


# Hand-made x86 code and programs (pieces of functions, the rebuild check): no compiler needed.
PUSH_FRAME = bytes.fromhex("558bec")  # push ebp; mov ebp, esp
CALL_0 = bytes.fromhex("e800000000")  # call (a function Ghidra took for one that never returns: throw)
EH_EPILOGUE = bytes.fromhex("8b4df45f8be55dc3")  # mov ecx, [ebp-0xc]; pop edi; mov esp, ebp; pop ebp; ret
FRAME_RET = PUSH_FRAME + bytes.fromhex("33c08be55dc3")  # ...; xor eax, eax; mov esp, ebp; pop ebp; ret
CATCH = bytes.fromhex("8b45ecc3")  # mov eax, [ebp-0x14]; ret: the frame's locals, after the function's return
UNWIND = bytes.fromhex("8d4df0e900000000")  # lea ecx, [ebp-0x10]; jmp <destructor>: a .text$x funclet


def test_a_piece_ghidra_cut_off_is_the_rest_of_the_function_before_it():
    from decompnet.arch import x86

    # a lone epilogue after a call Ghidra took for one that never returns: the code before runs on into it
    assert "runs on" in x86.piece(PUSH_FRAME + bytes.fromhex("6a00") + CALL_0, EH_EPILOGUE)
    assert "restores edi" in x86.piece(PUSH_FRAME + CALL_0, bytes.fromhex("5f5ec3"))  # pop edi; pop esi; ret
    # a catch block after the return of a function with an ebp frame
    assert "frame" in x86.piece(FRAME_RET, CATCH)
    # without a frame, a short jump from the code before says it continues there (push ebx; mov ebx, ecx; test eax,
    # eax; je +2; pop ebx; ret), then mov eax, [ebx+0x14]; pop ebx; ret
    assert "short jump" in x86.piece(bytes.fromhex("538bd985c074025bc3"), bytes.fromhex("8b43145bc3"))
    assert x86.piece(bytes.fromhex("538bd985c074005bc3"), bytes.fromhex("8b43145bc3")) is None  # (jumps within)


def test_whole_functions_and_exception_funclets_are_not_pieces():
    from decompnet.arch import x86

    assert x86.piece(FRAME_RET + b"\xcc\xcc", CATCH) is None  # padding ends a function
    assert x86.piece(FRAME_RET, PUSH_FRAME + bytes.fromhex("8b4508c3")) is None  # sets its frame before using it
    # MSVC's .text$x funclets use their parent's ebp without a frame, by design: each follows another, which ends
    # with a jmp and sets up no frame (the exception-handling stubs are set aside by their own rule)
    assert x86.piece(UNWIND, UNWIND) is None
    assert x86.piece(bytes.fromhex("b878563412e900000000"), UNWIND) is None  # mov eax, <FuncInfo>; jmp handler
    # a function setting its frame through __EH_prolog (mov eax, <handler>; call), or with enter
    assert x86.piece(FRAME_RET, bytes.fromhex("b878563412") + CALL_0 + bytes.fromhex("8b4508c3")) is None
    assert x86.piece(PUSH_FRAME + CALL_0, bytes.fromhex("c8800000608b450cc3")) is None
    # xor esi, esi only sets esi; a function saving what it restores is whole
    assert x86.piece(PUSH_FRAME + CALL_0, bytes.fromhex("33f68bc6c3")) is None
    assert x86.piece(PUSH_FRAME + CALL_0, bytes.fromhex("56be010000005ec3")) is None
    assert x86.piece(PUSH_FRAME + CALL_0 + b"\x8b", EH_EPILOGUE) is None  # code before ends mid-instruction: data
    # MSVC's alignment filler after a hand-written function (lea esp, [esp]; mov edi, edi) does nothing
    assert x86.piece(bytes.fromhex("578b7c2408eb6a"), bytes.fromhex("8da424000000008bff")) is None


TEXT = 0x401000


def _tiny_exe(code: bytes) -> bytes:
    """A 32-bit Windows program whose .text (at 0x401000) is `code`: just what read_pe and delink.py read."""
    pe_at, opt_bytes, raw_at = 0x40, 0xE0, 0x200
    head = bytearray(raw_at)
    head[:2] = b"MZ"
    struct.pack_into("<I", head, 0x3C, pe_at)
    head[pe_at:pe_at + 4] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", head, pe_at + 4, 0x14C, 1, 0, 0, 0, opt_bytes, 0x102)
    opt = pe_at + 24
    struct.pack_into("<H", head, opt, 0x10B)
    struct.pack_into("<I", head, opt + 16, TEXT - 0x400000)  # entry
    struct.pack_into("<I", head, opt + 28, 0x400000)  # image base
    struct.pack_into("<I", head, opt + 56, 0x2000)  # image size
    struct.pack_into("<8sIIIIIIHHI", head, opt + opt_bytes, b".text", len(code), TEXT - 0x400000, len(code), raw_at,
                     0, 0, 0, 0, 0x60000020)
    return bytes(head) + code


def _row(start: int, end: int) -> dict:
    return {"entry": start, "end": end, "name": f"FUN_{start:08x}", "source": "DEFAULT", "purge": 0,
            "convention": "__cdecl"}


# 0x401000 a function with a frame, cut by Ghidra at a call to 0x401020 (taken for one that never returns), holding
# its own piece's address (mov eax, 0x40100f); 0x40100f its epilogue; 0x401020 another function, which jumps to it
OWNER = PUSH_FRAME + bytes.fromhex("b80f104000") + bytes.fromhex("e813000000") + bytes.fromhex("6a00")
PROGRAM = (OWNER + EH_EPILOGUE).ljust(0x20, b"\x90") + PUSH_FRAME + bytes.fromhex("5dc3")
ROWS = [_row(0x401000, 0x40100F), _row(0x40100F, 0x401017), _row(0x401020, 0x401025)]


def test_a_piece_is_delinked_with_the_function_it_belongs_to():
    pe = scratch_pe.read_pe(_tiny_exe(PROGRAM))
    found = scratch_pe.analyse(pe, ROWS)
    assert [f.addr for f in found] == [0x401000, 0x40100F, 0x401020]
    functions, joined = scratch_pe.merge_pieces(pe, found, log=lambda *_: None)
    assert joined == {"_func_00401000": ["_func_0040100F"]}
    whole = functions[0]
    assert (whole.addr, whole.size) == (0x401000, 0x20)  # the epilogue and the padding after it
    # its piece's address is its own now (a label, as delink.py writes $L), the call still a relocation
    assert whole.relocs == [(4, scratch_pe.DIR32, None, 0xF), (9, scratch_pe.REL32, "_func_00401020", 0)]
    # matched functions stay as they are; so does a piece another function calls (Ghidra was right: a function)
    assert scratch_pe.merge_pieces(pe, found, keep={"_func_0040100F"}, log=lambda *_: None)[1] == {}
    called = (OWNER + EH_EPILOGUE).ljust(0x20, b"\x90") + bytes.fromhex("e8eaffffffc3")  # call 0x40100f; ret
    pe = scratch_pe.read_pe(_tiny_exe(called))
    assert scratch_pe.merge_pieces(pe, scratch_pe.analyse(pe, ROWS), log=lambda *_: None)[1] == {}


def test_a_function_found_in_a_gap_keeps_its_last_instruction_whole():
    """A `ret 8` ends with a zero byte, which the gap's padding was stripped with: the byte belonged to no
    function, its function's object ended mid-instruction, and the piece after it was never next to it."""
    code = bytes.fromhex("c3") + bytes.fromhex("33c0c20800") + PUSH_FRAME + bytes.fromhex("5dc3")
    pe = scratch_pe.read_pe(_tiny_exe(code))
    found = scratch_pe.analyse(pe, [_row(0x401000, 0x401001), _row(0x401006, 0x40100B)])
    assert [(f.addr, f.size) for f in found] == [(0x401000, 1), (0x401001, 5), (0x401006, 5)]


def test_functions_nothing_calls_are_cut_at_the_int3s_between_them():
    """Visual C++ 2005 and later pad each function of a file to 16 bytes with int3. A run of functions inlined
    everywhere (nothing calls them, so Ghidra misses them) was one function in a 2010 build of the demo: 14
    functions found of 19, and none of the five in the gaps could match."""
    pad = lambda code: code + b"\xcc" * (-len(code) % 16)
    code = (pad(bytes.fromhex("8b442404c20800")) + pad(bytes.fromhex("8bc1c3")) + pad(bytes.fromhex("33c0c3"))
            + pad(bytes.fromhex("8b442404c3")))
    pe = scratch_pe.read_pe(_tiny_exe(code))
    found = scratch_pe.analyse(pe, [_row(0x401000, 0x401007), _row(0x401030, 0x401035)])
    assert [(f.addr, f.size) for f in found] == [(0x401000, 7), (0x401010, 3), (0x401020, 3), (0x401030, 5)]


def test_the_padding_the_linker_aligns_every_function_with_doesnt_end_a_source_file():
    """Visual C++ 2005 on aligns each function to 16 bytes with int3: a 2005 game got a source file for almost every
    function (19,028 for 20,863). More than that padding still ends a file, and so does int3 where the compiler pads
    its functions itself (6.0, with nops) and the linker's int3 is rare: an object's end."""
    def files(blocks: list[bytes]) -> list[list[int]]:
        pe = scratch_pe.read_pe(_tiny_exe(b"".join(blocks)))
        starts = [TEXT + sum(map(len, blocks[:k])) for k in range(len(blocks)) if blocks[k][:1] != b"\xcc"]
        found = scratch_pe.analyse(pe, [_row(a, a + len(FRAME_RET)) for a in starts])
        return [[f.addr - TEXT for f in fns] for fns in scratch_pe.units(found, pe).values()]
    aligned, padded = FRAME_RET.ljust(16, b"\xcc"), FRAME_RET.ljust(16, b"\x90")
    assert files([aligned] * 6) == [[0, 0x10, 0x20, 0x30, 0x40, 0x50]]
    assert files([aligned] * 3 + [b"\xcc" * 16] + [aligned] * 3) == [[0, 0x10, 0x20], [0x40, 0x50, 0x60]]
    assert files([padded] * 3 + [aligned] + [padded] * 2) == [[0, 0x10, 0x20, 0x30], [0x40, 0x50]]
    assert files([aligned] * 300) == [list(range(0, 0x1000, 0x10)), list(range(0x1000, 0x12C0, 0x10))]  # (4 KiB)


def _project(tmp_path: Path, program: bytes, rows: list[dict], unit_bytes: int = 0x1000) -> Path:
    """A PC project's config and delinked objects for a hand-made program, as create writes them (no compiler)."""
    root = tmp_path / "proj"
    (root / "orig").mkdir(parents=True)
    (root / "orig" / "game.exe").write_bytes(_tiny_exe(program))
    pe = scratch_pe.read_pe(_tiny_exe(program))
    groups = {}
    for f in scratch_pe.analyse(pe, rows):  # (one source file per function, to move pieces between them)
        groups[f"text_{f.addr:08X}"] = [scratch_pe._entry(pe, f)]
    info = {"exe": "orig/game.exe", "sha1": hashlib.sha1(_tiny_exe(program)).hexdigest(), "image_base": 0x400000,
            "units": groups}
    (root / "config" / "game").mkdir(parents=True)
    (root / "config" / "game" / "functions.json").write_text(json.dumps(info))
    (root / "src" / "auto").mkdir(parents=True)
    for unit in groups:
        (root / "src" / "auto" / f"{unit}.cpp").write_text('#include "common.h"\n')
    _delink(root)
    return root


def _delink(root: Path) -> None:
    info = json.loads((root / "config" / "game" / "functions.json").read_text())
    exe = (root / info["exe"]).read_bytes()
    out = root / "build" / "game" / "target"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    delink = _template_tool("delink")
    for unit, functions in info["units"].items():
        (out / f"{unit}.obj").write_bytes(delink.coff(functions, exe))


def test_a_project_made_before_gets_its_pieces_joined_and_still_rebuilds(tmp_path):
    root = _project(tmp_path, PROGRAM, ROWS)
    assert scratch_pe.check_code(root, "game", log=lambda *_: None)["identical"]
    # the piece is the first function of its source file: it moves to the file before only when that file keeps
    # other code, and holds no C for it
    assert scratch_pe.merge_project_pieces(root, "game", log=lambda *_: None) == {}
    info = json.loads((root / "config" / "game" / "functions.json").read_text())
    info["units"]["text_0040100F"].append(info["units"].pop("text_00401020")[0])
    (root / "config" / "game" / "functions.json").write_text(json.dumps(info))
    piece_file = root / "src" / "auto" / "text_0040100F.cpp"
    piece_file.write_text("void Obj::func_0040100F(void) {\n}\n")
    assert scratch_pe.merge_project_pieces(root, "game", log=lambda *_: None) == {}
    piece_file.write_text('#include "common.h"\n')
    joined = scratch_pe.merge_project_pieces(root, "game", log=lambda *_: None)
    assert joined == {"_func_00401000": ["_func_0040100F"]}
    units = json.loads((root / "config" / "game" / "functions.json").read_text())["units"]
    assert [[f["name"] for f in fns] for fns in units.values()] == [["_func_00401000"], ["_func_00401020"]]
    assert units["text_00401000"][0]["size"] == 0x20
    _delink(root)
    assert scratch_pe.check_code(root, "game", log=lambda *_: None) == {
        "identical": True, "detail": "identical: the whole program (549 bytes); its 2 functions (37 bytes of code), "
                                     "0 of them from C", "what": "The game's program, rebuilt from the project,"}
    assert (root / "build" / "game" / "game.exe").read_bytes() == (root / "orig" / "game.exe").read_bytes()


def test_the_rebuild_check_finds_a_function_delinked_wrong(tmp_path):
    root = _project(tmp_path, PROGRAM, ROWS)
    exe, target = (root / "orig" / "game.exe").read_bytes(), root / "build" / "game" / "target" / "text_00401000.obj"
    owner = json.loads((root / "config" / "game" / "functions.json").read_text())["units"]["text_00401000"][0]
    coff = _template_tool("delink").coff
    target.write_bytes(coff([{**owner, "relocs": [[9, scratch_pe.REL32, "_func_0040100F", 0]]}], exe))  # wrong call
    result = scratch_pe.check_code(root, "game", log=lambda *_: None)
    assert result == {"identical": False,
                      "detail": "1 functions rebuilt from the delinked objects differ from your copy: _func_00401000"}
    target.write_bytes(coff([{**owner, "size": owner["size"] + 1}], exe))  # cut one byte too long
    result = scratch_pe.check_code(root, "game", log=lambda *_: None)
    assert not result["identical"] and "_func_0040100F (overlaps _func_00401000)" in result["detail"]


def _compiled(code: bytes, name: str, relocs: list[tuple[int, int, int]], data: bytes = b"") -> bytes:
    """An object as Visual C++ compiles one: .text holding the function `name` (symbol 0), .data holding `data` (a
    string, $SG1: symbol 1), and the code's relocations as (offset, symbol, type)."""
    text_at = 20 + 40 * 2
    rel_at = text_at + len(code)
    data_at = rel_at + 10 * len(relocs)
    table_at = data_at + len(data)
    strings = bytearray(4)
    table = b""
    for sym, value, section, typ, cls in ((name, 0, 1, 0x20, 2), ("$SG1", 0, 2, 0, 3)):
        raw = sym.encode()
        field = raw.ljust(8, b"\0") if len(raw) <= 8 else struct.pack("<II", 0, len(strings))
        strings += b"" if len(raw) <= 8 else raw + b"\0"
        table += struct.pack("<8sIhHBB", field, value, section, typ, cls, 0)
    struct.pack_into("<I", strings, 0, len(strings))
    return (struct.pack("<HHIIIHH", 0x14C, 2, 0, table_at, 2, 0, 0)
            + struct.pack("<8sIIIIIIHHI", b".text", 0, 0, len(code), text_at, rel_at, 0, len(relocs), 0, 0x60500020)
            + struct.pack("<8sIIIIIIHHI", b".data", 0, 0, len(data), data_at, 0, 0, 0, 0, 0xC0300040)
            + code + b"".join(struct.pack("<IIH", *r) for r in relocs) + data + table + bytes(strings))


def test_the_project_rebuilds_the_program_with_each_function_from_its_c_where_that_is_the_games_code(tmp_path):
    """The rebuildable export on PC: tools/rebuild.py puts the whole program back together, each function from what
    src/ compiles to where that is the game's code once its relocations are filled in (its own string where the game
    has the same bytes), else from the function delinked from the user's copy; a best attempt that doesn't match, or
    calls another function, stays the game's bytes, and the program is the user's copy either way."""
    root = _project(tmp_path, PROGRAM, ROWS)
    exe, compiled = (root / "orig" / "game.exe").read_bytes(), root / "build" / "game" / "src" / "auto"
    compiled.mkdir(parents=True)
    info = json.loads((root / "config" / "game" / "functions.json").read_text())
    coff = _template_tool("delink").coff
    for unit, fns in info["units"].items():  # (C that compiles to each function: its own object, as delinked)
        (compiled / f"{unit}.obj").write_bytes(coff(fns, exe))

    def built() -> list[str]:
        result = scratch_pe.check_code(root, "game", log=lambda *_: None)
        assert result["identical"], result
        assert (root / "build" / "game" / "game.exe").read_bytes() == exe
        return json.loads((root / "build" / "game" / "rebuild.json").read_text())["c"]
    assert built() == ["_func_00401000", "_func_0040100F", "_func_00401020"]
    owner = info["units"]["text_00401000"][0]
    (compiled / "text_00401000.obj").write_bytes(coff([{**owner, "relocs": [[9, scratch_pe.REL32, "_func_0040100F", 0]]}],
                                                      exe))  # calls another function
    assert built() == ["_func_0040100F", "_func_00401020"]
    wrong = bytearray(exe)
    wrong[scratch_pe.read_pe(exe).offset(0x401020) + 1] ^= 1  # another instruction
    (compiled / "text_00401020.obj").write_bytes(coff(info["units"]["text_00401020"], bytes(wrong)))
    assert built() == ["_func_0040100F"]
    # a string of its own: used where the game's code reads it from, when the game has the same bytes there
    load = bytes.fromhex("b810104000c3")  # mov eax, 0x401010; ret
    program = load.ljust(0x10, b"\x90") + b"hi\0"
    root = _project(tmp_path / "string", program, [_row(0x401000, 0x401006)])
    exe, compiled = (root / "orig" / "game.exe").read_bytes(), root / "build" / "game" / "src" / "auto"
    compiled.mkdir(parents=True)
    code = bytes.fromhex("b800000000c3")
    (compiled / "text_00401000.obj").write_bytes(_compiled(code, "_func_00401000", [(1, 1, scratch_pe.DIR32)], b"hi\0"))
    assert built() == ["_func_00401000"]
    (compiled / "text_00401000.obj").write_bytes(_compiled(code, "_func_00401000", [(1, 1, scratch_pe.DIR32)], b"ho\0"))
    assert built() == []


def test_agents_join_two_functions_by_their_names(tmp_path):
    root = _project(tmp_path, PROGRAM, ROWS)
    with pytest.raises(ForkError, match="is matched"):
        scratch_pe.join_functions(root, "game", "_func_00401000", "_func_0040100F", keep={"_func_0040100F"})
    with pytest.raises(ForkError, match="would be left with no code"):  # (each was given a file of its own)
        scratch_pe.join_functions(root, "game", "_func_00401000", "_func_0040100F")
    info = json.loads((root / "config" / "game" / "functions.json").read_text())
    info["units"]["text_00401000"] += info["units"].pop("text_0040100F")
    (root / "config" / "game" / "functions.json").write_text(json.dumps(info))
    said = scratch_pe.join_functions(root, "game", "_func_0040100F", "_func_00401000")
    assert said == "_func_0040100F joined to _func_00401000"
    _delink(root)
    assert scratch_pe.check_code(root, "game", log=lambda *_: None)["identical"]
    with pytest.raises(ForkError, match="isn't one of the game's functions"):
        scratch_pe.join_functions(root, "game", "_func_0040100F", "_func_00401020")


def _one_file(root: Path) -> None:
    """The hand-made project's functions all in one source file, text_00401000."""
    info = json.loads((root / "config" / "game" / "functions.json").read_text())
    info["units"] = {"text_00401000": [fn for fns in info["units"].values() for fn in fns]}
    (root / "config" / "game" / "functions.json").write_text(json.dumps(info))
    for path in (root / "src" / "auto").glob("*.cpp"):
        if path.stem != "text_00401000":
            path.unlink()


def test_a_source_file_is_divided_at_a_function_and_keeps_its_compiler(tmp_path):
    """split_file on PC: the file's unit in functions.json ends where the function starts, and a new one, right after
    it, holds the rest, built with the file's compiler; the code still rebuilds byte for byte."""
    root = _project(tmp_path, PROGRAM, ROWS)
    _one_file(root)
    source, new = "src/auto/text_00401000.cpp", "src/auto/text_00401020.cpp"
    compilers = scratch_pe.compilers_file(root, "game")
    compilers.write_text(json.dumps({source: "7.1"}))
    for at, name, why in (("_func_00401000", new, "after its first"), ("_func_0040FFFF", new, "after its first"),
                          ("_func_00401020", "src/game/b.cpp", "src/auto/<name>.cpp"),
                          ("_func_00401020", source, "exists already")):
        with pytest.raises(ForkError, match=re.escape(why)):
            scratch_pe.split_unit(root, "game", source, at, name)
    with pytest.raises(ForkError, match="isn't one of the project's source files"):
        scratch_pe.split_unit(root, "game", "src/auto/nope.cpp", "_func_00401020", new)
    config = root / "config" / "game" / "functions.json"
    before = config.read_text()
    saved = scratch_pe.split_unit(root, "game", source, "_func_00401020", new)
    assert saved == {config: before, compilers: json.dumps({source: "7.1"})}
    units = json.loads(config.read_text())["units"]
    assert {u: [fn["name"] for fn in fns] for u, fns in units.items()} == {
        "text_00401000": ["_func_00401000", "_func_0040100F"], "text_00401020": ["_func_00401020"]}
    assert scratch_pe.file_compilers(root, "game") == {source: "7.1", new: "7.1"}
    _delink(root)
    assert scratch_pe.check_code(root, "game", log=lambda *_: None)["identical"]


def test_each_pc_source_file_can_have_its_own_visual_cpp(tmp_path):
    """file_compiler, set_file_compiler, file_ranges and set_own_compiler on a PC project: what try_compiler and the
    Settings panel's compiler per file stand on. A project whose configure.py reads no map has none."""
    root = _project(tmp_path, PROGRAM, ROWS)
    pc = consoles.get("pc")
    manifest = {"created": "from scratch", "platform": "pc", "version": "game", "compiler": "MSVC/6.0"}
    (root / ".decomp.wtf.json").write_text(json.dumps(manifest))
    shutil.copytree(Path(scratch_pe.__file__).with_name("project_template") / "tools", root / "tools")
    fp = ForkProject(root)
    first = "src/auto/text_00401000.cpp"
    assert pc.file_compiler(fp, first) is None  # (made before: no map)
    scratch_pe.compilers_file(root, "game").write_text("{}\n")
    assert pc.file_compiler(fp, first) == pc.file_compiler(fp, "") == "MSVC/6.0"
    assert pc.set_file_compiler(fp, first, "MSVC/7.1") == "config/game/file_compilers.json"
    assert pc.file_compiler(fp, first) == "MSVC/7.1" and pc.file_compiler(fp, "src/auto/text_0040100F.cpp") == "MSVC/6.0"
    assert pc.file_ranges(fp)[first] == [(0x401000, 0x40100F)]
    sources = sorted(pc.file_ranges(fp))
    changed = pc.set_own_compiler(fp, "MSVC/7.1", sources)
    assert changed == ["config/game/file_compilers.json", "tools/configure.py", ".decomp.wtf.json"]
    assert pc.file_compiler(fp, "") == "MSVC/7.1" and 'MSVC_VERSION = "7.1"' in (root / "tools/configure.py").read_text()
    assert scratch_pe.file_compilers(root, "game") == {s: "6.0" for s in sources if s != first}
    pc.set_file_compiler(fp, first, "MSVC/7.1")  # the project's own: no line
    assert first not in scratch_pe.file_compilers(root, "game")


# a function that calls an import through its thunk, the thunks (RtlUnwind's, closesocket's), then a function that
# calls the second thunk; the import address table's slots are at the end of the code
SLOTS = {0x401F00: "RtlUnwind", 0x401F04: "closesocket"}
CALLS_THUNKS = (bytes.fromhex("e80b000000c3").ljust(0x10, b"\x90")  # call 0x401010; ret
                + bytes.fromhex("ff25001f4000") + bytes.fromhex("ff25041f4000").ljust(0xA, b"\x90")
                + bytes.fromhex("e8f1ffffffc3"))  # call 0x401016; ret


def test_import_thunks_are_functions_of_their_own_named_after_their_imports(monkeypatch):
    """Ghidra marks the linker's `jmp [__imp__Name]` thunks as thunks, which analyse used to leave out: their bytes
    went to the function before them, and calls to the second one were calls into its middle (_D_00401016)."""
    monkeypatch.setattr(scratch_pe, "_imports", lambda pe, opt: dict(SLOTS))
    pe = scratch_pe.read_pe(_tiny_exe(CALLS_THUNKS))
    assert scratch_pe.import_thunk(pe, 0x401010) == "RtlUnwind" and scratch_pe.import_thunk(pe, 0x401000) is None
    rows = [_row(0x401000, 0x401006), {**_row(0x401010, 0x401016), "name": "RtlUnwind", "thunk": True,
                                        "convention": "__stdcall", "purge": 16},
            {**_row(0x401016, 0x40101C), "name": "closesocket", "thunk": True, "purge": 2147483647},
            _row(0x401020, 0x401026)]
    found = scratch_pe.analyse(pe, rows)
    assert [(f.name, f.size) for f in found] == [("_func_00401000", 0x10), ("_RtlUnwind@16", 6),
                                                 ("_closesocket", 0xA), ("_func_00401020", 6)]
    assert found[0].relocs == [(1, scratch_pe.REL32, "_RtlUnwind@16", 0)]
    assert found[1].relocs == [(2, scratch_pe.DIR32, "__imp__RtlUnwind", 0)]
    assert found[3].relocs == [(1, scratch_pe.REL32, "_closesocket", 0)]


def test_a_project_made_before_gets_its_import_thunks_cut_out_named_and_set_aside(tmp_path, monkeypatch):
    """The thunks a project made before took for one function (found in a gap) become functions of their own, the
    caller of the second calls it by name, functions.json sets them aside and names them as their imports (so C
    declaring `__stdcall RtlUnwind` pairs with them), and the code still rebuilds byte for byte."""
    monkeypatch.setattr(scratch_pe, "_imports", lambda pe, opt: dict(SLOTS))
    rows = [_row(0x401000, 0x401006), _row(0x401020, 0x401026)]
    root = _project(tmp_path, CALLS_THUNKS, rows)
    config = root / "config" / "game" / "functions.json"
    before = config.read_text()
    units = json.loads(before)["units"]
    assert [(fns[0]["name"], fns[0]["size"]) for fns in units.values()][1] == ("_func_00401010", 0x10)
    assert units["text_00401020"][0]["relocs"] == [[1, scratch_pe.REL32, "_D_00401016", 0]]
    # a matched function calling into the run: its C names it as it is, so the run stays
    assert scratch_pe.name_import_thunks(root, "game", keep={"_func_00401020"}) == 0
    assert config.read_text() == before
    assert scratch_pe.name_import_thunks(root, "game") == 2
    info = json.loads(config.read_text())
    entries = {fn["name"]: fn for fns in info["units"].values() for fn in fns}
    assert {n: (fn["size"], fn.get("generated", "")[:41]) for n, fn in entries.items()} == {
        "_func_00401000": (0x10, ""), "_RtlUnwind": (6, "an import thunk the linker adds for calls"),
        "_closesocket": (0xA, "an import thunk the linker adds for calls"), "_func_00401020": (6, "")}
    assert entries["_func_00401000"]["relocs"] == [[1, scratch_pe.REL32, "_RtlUnwind", 0]]
    assert entries["_func_00401020"]["relocs"] == [[1, scratch_pe.REL32, "_closesocket", 0]]
    assert info["names"] == {"func_00401010": "RtlUnwind", "func_00401016": "closesocket"}
    names = _template_tool("names")
    assert names.address_of(b"_RtlUnwind@16", names.real_names(info)) == b"func_00401010"
    assert consoles.get("pc").generated_functions(ForkProject(root), "game")["_closesocket"].startswith("an import")
    _delink(root)
    rebuild = _template_tool("rebuild")  # (the tiny program has no import table: the rebuild reads SLOTS too)
    monkeypatch.setattr(rebuild.Program, "_imports", lambda self, exe, at, base: {
        f"__imp__{name}": slot for slot, name in SLOTS.items()})
    monkeypatch.setattr(scratch_pe, "_template_tool", lambda name: rebuild)
    assert scratch_pe.check_code(root, "game", log=lambda *_: None)["identical"]
    assert scratch_pe.name_import_thunks(root, "game") == 0  # (done)


def test_only_the_frame_helpers_shapes_set_a_frame():
    from decompnet.arch import x86

    after_one = bytes.fromhex("6a0c") + CALL_0 + bytes.fromhex("59c3")  # push 0xc; call _lock; pop ecx; ret
    assert x86.piece(after_one, bytes.fromhex("8b75e4")) is None  # mov esi, [ebp-0x1c]: not this function's
    seh_prolog = bytes.fromhex("6a0c6888204000") + CALL_0 + bytes.fromhex("33dbc3")  # push 0xc; push <table>; call
    assert "frame" in x86.piece(seh_prolog, bytes.fromhex("ff7508c3"))  # push [ebp+8]; ...: its __finally
    eh_prolog3 = bytes.fromhex("6a04b8dd9b8d00") + CALL_0 + bytes.fromhex("c3")  # push 4; mov eax, <h>; call
    assert "frame" in x86.piece(eh_prolog3, CATCH)
    assert x86.piece(bytes.fromhex("53b8dd9b8d00") + CALL_0 + bytes.fromhex("5bc3"), CATCH) is None  # push ebx first


# a function with two jump tables, one right after the other: jmp [eax*4+T1]; jmp [eax*4+T2]; ret; ret; T1; T2
TWO_TABLES = (bytes.fromhex("ff2485") + struct.pack("<I", 0x401010) + bytes.fromhex("ff2485") + struct.pack("<I", 0x401018)
              + bytes.fromhex("c3c3") + struct.pack("<4I", 0x40100E, 0x40100F, 0x40100F, 0x40100E))


def test_a_jump_table_ends_where_the_next_one_starts(tmp_path):
    """Each entry of the second table was relocated twice (23 functions of a PC game): the code rebuilt from the
    objects had those entries doubled. Projects made before get the repeats dropped."""
    pe = scratch_pe.read_pe(_tiny_exe(TWO_TABLES))
    (fn,) = scratch_pe.analyse(pe, [_row(0x401000, 0x401010)])
    assert [r[0] for r in fn.relocs] == [3, 10, 16, 20, 24, 28]  # the tables' addresses, then four entries, once
    root = _project(tmp_path, TWO_TABLES, [_row(0x401000, 0x401010)])
    config = root / "config" / "game" / "functions.json"
    info = json.loads(config.read_text())
    entry = info["units"]["text_00401000"][0]
    entry["relocs"] += entry["relocs"][-2:]  # as analyse made them before
    config.write_text(json.dumps(info))
    _delink(root)
    assert not scratch_pe.check_code(root, "game", log=lambda *_: None)["identical"]
    assert scratch_pe.drop_repeated_relocations(root, "game") == 1
    assert scratch_pe.drop_repeated_relocations(root, "game") == 0
    _delink(root)
    assert scratch_pe.check_code(root, "game", log=lambda *_: None)["identical"]


def test_a_far_call_is_not_taken_for_a_call_to_another_function(tmp_path):
    """Bytes Ghidra took for code in a 2005 PC game held `lcall 0x300:0`: analyse relocated its address as a near
    call's, and the code rebuilt from the objects had it changed, so the rebuild check could never pass."""
    far_call = PUSH_FRAME + bytes.fromhex("9a000000000003") + bytes.fromhex("e800100000") + bytes.fromhex("5dc3")
    pe = scratch_pe.read_pe(_tiny_exe(far_call))
    [fn] = scratch_pe.analyse(pe, [_row(0x401000, 0x401000 + len(far_call))])
    assert [r[0] for r in fn.relocs] == [11]  # the near call's target only
    root = _project(tmp_path, far_call, [_row(0x401000, 0x401000 + len(far_call))])
    config = root / "config" / "game" / "functions.json"
    info = json.loads(config.read_text())
    info["units"]["text_00401000"][0]["relocs"].insert(0, [6, scratch_pe.REL32, "_D_00000300", 0])  # as before
    config.write_text(json.dumps(info))
    _delink(root)
    assert not scratch_pe.check_code(root, "game", log=lambda *_: None)["identical"]
    assert scratch_pe.drop_far_call_relocations(root, "game") == 1
    assert scratch_pe.drop_far_call_relocations(root, "game") == 0
    _delink(root)
    assert scratch_pe.check_code(root, "game", log=lambda *_: None)["identical"]


def test_a_project_made_before_gets_its_functions_last_instructions_whole(tmp_path):
    """Found in a gap, `jmp <next function>` (e9 0a 00 00 00) lost its zeros with the padding after it: the function
    was 2 bytes, and no C could match it. 465 functions of a PC game ended mid-instruction."""
    program = bytes.fromhex("c3") + bytes.fromhex("e90a000000") + b"\x90" * 10 + PUSH_FRAME + bytes.fromhex("5dc3")
    rows = [_row(0x401000, 0x401001), _row(0x401010, 0x401015)]
    root = _project(tmp_path, program, rows)
    config = root / "config" / "game" / "functions.json"
    info = json.loads(config.read_text())
    assert info["units"]["text_00401001"][0]["size"] == 15  # (as analyse makes it now)
    info["units"]["text_00401001"][0].update(size=2, relocs=[])  # as it made it before
    config.write_text(json.dumps(info))
    assert scratch_pe.complete_last_instructions(root, "game", keep={"_func_00401001"}) == 0
    assert scratch_pe.complete_last_instructions(root, "game") == 1
    entry = json.loads(config.read_text())["units"]["text_00401001"][0]
    assert entry["size"] == 15 and entry["relocs"] == [[1, scratch_pe.REL32, "_func_00401010", 0]]
    assert scratch_pe.complete_last_instructions(root, "game") == 0
    _delink(root)
    assert scratch_pe.check_code(root, "game", log=lambda *_: None)["identical"]


# Exception-handling stubs (Visual C++'s .text$x): a 2000s Visual C++ 6.0 game's last 4,677 functions were stubs,
# the smallest of its 11,877, so agents were handed them first though no C of their own makes them.
CODE_AT = 0x401000
FRAME_FUNCTION = bytes.fromhex("55 8bec 8b4508 5d c3")  # push ebp; mov ebp, esp; mov eax, [ebp+8]; pop ebp; ret
LONE_EPILOGUE = bytes.fromhex("8b45f8 c9 c3")  # a piece Ghidra cut off a function: mov eax, [ebp-8]; leave; ret
EH_PROLOG = bytes.fromhex("b800204000 e800000000 51 c3")  # mov eax, <handler>; call __EH_prolog; push ecx; ret
# the C runtime's __SEH_prolog, which sets ebp with a lea: push <handler>; push fs:[0]; mov eax, [esp+0x10];
# mov [esp+0x10], ebp; lea ebp, [esp+0x10]; sub esp, eax; push ebx; push esi; push edi; mov eax, [ebp-8]
SEH_PROLOG = bytes.fromhex("6800104000 64ff3500000000 8b442410 896c2410 8d6c2410 2be0 53 56 57 8b45f8 c3")
EH_STUBS = [bytes.fromhex("8d4dec e900000000"),  # lea ecx, [ebp-0x14]; jmp <destructor>
            bytes.fromhex("b810204000 e900000000"),  # mov eax, <FuncInfo>; jmp __CxxFrameHandler
            bytes.fromhex("8b4df0 e900000000"),  # mov ecx, [ebp-0x10]; jmp <destructor> (new in progress)
            # an array's: push <destructor>; push 2; push 4; lea eax, [ebp-0x20]; push eax; call <iterator>; ret
            bytes.fromhex("6800104000 6a02 6a04 8d45e0 50 e800000000 c3"),
            bytes.fromhex("b900304000 e900000000")]  # mov ecx, <global>; jmp <destructor>


def _tiny_pe(functions: list[bytes]) -> tuple[bytes, list[tuple[int, int]]]:
    """A 32-bit Windows program whose .text holds `functions` one after another (at CODE_AT), and their (address,
    size): enough for read_pe."""
    code = b"".join(functions)
    pe_at, optional_bytes, file_align = 0x40, 0xE0, 0x200
    head = bytearray(file_align)
    head[:2], head[0x3C:0x40] = b"MZ", struct.pack("<I", pe_at)
    struct.pack_into("<4sHHIIIHH", head, pe_at, b"PE\0\0", 0x14C, 1, 0, 0, 0, optional_bytes, 0x10F)
    opt = pe_at + 0x18
    struct.pack_into("<H", head, opt, 0x10B)
    struct.pack_into("<I", head, opt + 16, CODE_AT - 0x400000)
    struct.pack_into("<I", head, opt + 28, 0x400000)
    struct.pack_into("<I", head, opt + 56, 0x1000 + len(code))
    struct.pack_into("<8sIIII", head, opt + optional_bytes, b".text", len(code), 0x1000, len(code), file_align)
    struct.pack_into("<I", head, opt + optional_bytes + 36, 0x60000020)
    extents, at = [], CODE_AT
    for fn in functions:
        extents.append((at, len(fn)))
        at += len(fn)
    return bytes(head) + code, extents


def test_exception_handling_stubs_are_found_only_where_the_linker_puts_them():
    """Stubs end the program's code (.text$x goes after all of .text). The same shapes in the main body are pieces
    Ghidra cut off real functions, and a real function building its frame through __EH_prolog isn't one."""
    main = [FRAME_FUNCTION, LONE_EPILOGUE] + [FRAME_FUNCTION] * (scratch_pe.EH_STUB_GAP + 1)
    data, extents = _tiny_pe(main + [SEH_PROLOG, EH_PROLOG] + EH_STUBS)
    found = scratch_pe.exception_stubs(scratch_pe.read_pe(data), extents)
    assert found == {addr for addr, _ in extents[-len(EH_STUBS):]}
    # without a handler, a few funclet shapes at the end are something else
    data, extents = _tiny_pe(main + [EH_STUBS[0], EH_STUBS[2]])
    assert scratch_pe.exception_stubs(scratch_pe.read_pe(data), extents) == set()
    # Visual C++ 2005 on: dynamic initializers follow the run (a 2000s game rebuilt with Visual C++ 2008 tools had 37,
    # a global's destructor among them), so the run must be long; a short one there is pieces of the main body
    initializers = [FRAME_FUNCTION] * 10 + [EH_STUBS[-1]] + [FRAME_FUNCTION] * 10
    stubs = EH_STUBS * (scratch_pe.EH_STUB_FOLLOWED_RUN // len(EH_STUBS) + 1)
    data, extents = _tiny_pe(main + stubs + initializers)
    found = scratch_pe.exception_stubs(scratch_pe.read_pe(data), extents)
    assert found == {addr for addr, _ in extents[len(main):len(main) + len(stubs)]}
    data, extents = _tiny_pe(main + EH_STUBS + initializers)
    assert scratch_pe.exception_stubs(scratch_pe.read_pe(data), extents) == set()


def _coff_sections(obj: bytes) -> list[tuple[str, bytes]]:
    """(name, raw bytes) of a COFF object's sections."""
    count, symbols_at, symbol_count = struct.unpack_from("<HxxxxII", obj, 2)
    strings = symbols_at + 18 * symbol_count
    out = []
    for i in range(count):
        name, size, raw = struct.unpack_from("<8s8xII", obj, 20 + 40 * i)
        name = name.rstrip(b"\0").decode()
        if name.startswith("/"):  # a long name, in the string table
            name = obj[strings + int(name[1:]):].split(b"\0", 1)[0].decode()
        out.append((name, obj[raw:raw + size]))
    return out


@pytest.mark.parametrize("version", ["6.0", "7.0", "7.1"])
@pytest.mark.skipif(toolchains.installed("msvc") is None
                    or (sys.platform != "win32" and toolchains.installed("wibo") is None),
                    reason="needs Visual C++ (msvc, proprietary) and wibo")
def test_the_stubs_visual_cpp_makes_are_found(tmp_path, version):
    """What each Visual C++ of the pack makes with /GX for functions with local objects to destroy: the functions
    stay, every stub of their .text$x is found (cut as Ghidra cuts them, after each jmp or ret)."""
    import capstone

    msvc = toolchains.path("msvc") / version / "Bin"
    cl = next((p for p in (msvc / "CL.EXE", msvc / "cl.exe") if p.exists()), None)
    if cl is None:
        pytest.skip(f"the pack has no Visual C++ {version}")
    (tmp_path / "t.cpp").write_text("struct Obj { int v; Obj(); ~Obj(); };\nvoid use(Obj *o);\n"
                                    "int one(int x) { Obj a; use(&a); return x; }\n"
                                    "int two(int x) { Obj a; Obj b[3]; use(&a); use(b); Obj *p = new Obj; use(p); "
                                    "return x; }\n")
    wibo = [str(toolchains.binary("wibo"))] if sys.platform != "win32" else []
    subprocess.run([*wibo, str(cl), "/nologo", "/c", "/GX", "/O2", "t.cpp"], cwd=tmp_path, check=True,
                   capture_output=True, timeout=120)
    sections = _coff_sections((tmp_path / "t.obj").read_bytes())
    functions = [raw for name, raw in sections if name == ".text" and raw]
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    stubs = []
    for raw in (raw for name, raw in sections if name == ".text$x"):
        start = 0
        for ins in md.disasm(raw, 0):
            if ins.mnemonic in ("jmp", "ret"):
                stubs.append(raw[start:ins.address + ins.size])
                start = ins.address + ins.size
    assert len(functions) == 2 and len(stubs) >= 4
    data, extents = _tiny_pe(functions + stubs)
    found = scratch_pe.exception_stubs(scratch_pe.read_pe(data), extents)
    assert found == {addr for addr, _ in extents[len(functions):]}


def _project_with_stubs(tmp_path, monkeypatch):
    """A PC project made before its import set stubs aside: a program whose code ends with stubs, its functions.json
    without `generated`, a manifest without `analysis`, measured once."""
    from decompnet.app.engine import RomNetwork
    from decompnet.forks.project import Unit

    net = RomNetwork(tmp_path / "decomp.db", tmp_path / "ws")
    root = tmp_path / "ws" / "p"
    data, extents = _tiny_pe([FRAME_FUNCTION] * 6 + EH_STUBS)
    (root / "orig").mkdir(parents=True)
    (root / "orig" / "v.exe").write_bytes(data)
    entries = [{"name": f"_func_{addr:08X}", "addr": addr, "offset": addr - CODE_AT + 0x200, "size": size,
                "relocs": []} for addr, size in extents]
    (root / "config" / "v").mkdir(parents=True)
    (root / "config" / "v" / "functions.json").write_text(json.dumps({"exe": "orig/v.exe", "units": {"u": entries}},
                                                                     indent=1))
    (root / "src" / "auto").mkdir(parents=True)
    (root / "src" / "auto" / "u.cpp").write_text('#include "common.h"\n')
    (root / "objdiff.json").write_text(json.dumps({"units": []}))
    (root / ".decomp.wtf.json").write_text(json.dumps({"created": "from scratch", "platform": "pc", "version": "v",
                                                       "tools": scratch_pe.tools_version()}))
    (root / ".gitignore").write_text("orig/\n")
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"],
                ["git", "-c", "user.name=t", "-c", "user.email=t@t.invalid", "commit", "-q", "-m", "start"]):
        subprocess.run(cmd, cwd=root, check=True, capture_output=True)
    net.forks._register("p", "P", "fork", root, {"kind": "fork", "origin": "scratch", "version": "v",
                                                  "platform": "pc", "ready": True}, "x")
    report = {"units": [{"name": "u", "functions": [{"name": e["name"], "size": str(e["size"]),
                                                     "fuzzy_match_percent": 0.0} for e in entries]}]}
    monkeypatch.setattr(ForkProject, "report", lambda self: report)
    monkeypatch.setattr(ForkProject, "units", lambda self: [Unit("u", None, None, "src/auto/u.cpp", False, None)])
    monkeypatch.setattr(ForkProject, "setup_commands", lambda self, version: [])
    net.forks.sync("p", log=lambda *_: None)
    return net, root, [e["name"] for e in entries[-len(EH_STUBS):]]


def test_a_project_made_before_gets_its_stubs_set_aside_once(tmp_path, monkeypatch):
    """The plan's migration: at start, functions.json says which functions are stubs, measuring sets them aside with
    why, the project's feed says how many, and the next start leaves the project alone."""
    net, root, stubs = _project_with_stubs(tmp_path, monkeypatch)
    pid = net.project_row("p")["id"]
    assert net.store.one("SELECT COUNT(*) AS n FROM functions WHERE project_id=? AND status='open'", pid)["n"] == 11
    consoles.get("pc").on_start(net, "p", log=lambda *_: None)
    info = json.loads((root / "config" / "v" / "functions.json").read_text())
    assert [fn["name"] for fn in info["units"]["u"] if fn.get("generated")] == stubs
    rows = {r["name"]: r for r in net.store.all("SELECT * FROM functions WHERE project_id=?", pid)}
    for name, row in rows.items():
        assert row["status"] == ("asm" if name in stubs else "open"), name
    from decompnet import network

    assert network.set_aside(rows[stubs[0]])["set_aside"] == scratch_pe.EH_STUB_WHY
    assert json.loads((root / ".decomp.wtf.json").read_text())["analysis"] == consoles.get("pc").ANALYSIS
    said = net.store.one("SELECT detail FROM events WHERE project_id=? AND kind='analysis'", pid)["detail"]
    assert said.startswith(f"{len(EH_STUBS)} functions are exception-handling code")
    log = subprocess.run(["git", "log", "--oneline"], cwd=root, capture_output=True, text=True).stdout
    assert "Analyse the game again" in log and not subprocess.run(["git", "status", "--porcelain", "config"], cwd=root,
                                                                  capture_output=True, text=True).stdout.strip()
    # given back to the agents, a stub stays theirs: measuring again doesn't set it aside again
    net.reopen(stubs[0], "p")
    net.forks.sync("p", log=lambda *_: None)
    assert net._function(stubs[0], "p")["status"] == "open" and net._function(stubs[1], "p")["status"] == "asm"
    consoles.get("pc").on_start(net, "p", log=lambda *_: None)  # (already done: nothing new in the feed)
    assert net.store.one("SELECT COUNT(*) AS n FROM events WHERE project_id=? AND kind='analysis'", pid)["n"] == 1


def test_a_project_without_the_program_in_it_waits_for_it(tmp_path, monkeypatch):
    net, root, stubs = _project_with_stubs(tmp_path, monkeypatch)
    (root / "orig" / "v.exe").unlink()
    consoles.get("pc").on_start(net, "p", log=lambda *_: None)
    assert "analysis" not in json.loads((root / ".decomp.wtf.json").read_text())
    assert net._function(stubs[0], "p")["status"] == "open"


def _project_made_before(tmp_path, monkeypatch, level: int, tools: dict[str, str] | None = None):
    """A PC project as a decompdecomp of analysis `level` left it: a function with a lone epilogue Ghidra cut off
    (PROGRAM), a function with a frame, then Visual C++'s stubs; below 2 one function with a jump table's entries
    relocated twice, from 3 its stubs marked. Its build is the delinked objects, rebuilt for each check. `tools`: its
    tools/ files, as an older template made them (else its tools are the template's, and the manifest says so)."""
    from decompnet.app.engine import RomNetwork
    from decompnet.consoles.pc import WindowsPC
    from decompnet.forks.project import Unit

    framed = [FRAME_RET] * (scratch_pe.EH_STUB_GAP + 1)  # (the main body's end: no stub's shape in a row)
    program = PROGRAM.ljust(0x30, b"\x90") + b"".join(framed + EH_STUBS)
    rows, at = list(ROWS), 0x401030
    for stub in framed + EH_STUBS:
        rows.append(_row(at, at + len(stub)))
        at += len(stub)
    net = RomNetwork(tmp_path / "decomp.db", tmp_path / "ws")
    root = tmp_path / "ws" / "p"
    (root / "orig").mkdir(parents=True)
    (root / "orig" / "game.exe").write_bytes(_tiny_exe(program))
    pe = scratch_pe.read_pe(_tiny_exe(program))
    entries = [scratch_pe._entry(pe, f) for f in scratch_pe.analyse(pe, rows)]
    if level < 2:
        entries[0]["relocs"].append(entries[0]["relocs"][0])
    if level >= 3:
        for fn in entries[-len(EH_STUBS):]:
            fn["generated"] = scratch_pe.EH_STUB_WHY
    (root / "config" / "game").mkdir(parents=True)
    (root / "config" / "game" / "functions.json").write_text(json.dumps({"exe": "orig/game.exe", "units": {"u": entries}}))
    (root / "src" / "auto").mkdir(parents=True)
    (root / "src" / "auto" / "u.cpp").write_text('#include "common.h"\n')
    (root / "objdiff.json").write_text(json.dumps({"units": []}))
    (root / ".decomp.wtf.json").write_text(json.dumps({"created": "from scratch", "platform": "pc", "version": "game",
                                                       "game": {"path": "orig/game.exe"},
                                                       **({} if tools else {"tools": scratch_pe.tools_version()}),
                                                       **({"analysis": level} if level > 1 else {})}))
    (root / ".gitignore").write_text("orig/\nbuild/\n")
    for name, text in (tools or {}).items():
        (root / "tools").mkdir(exist_ok=True)
        (root / "tools" / name).write_text(text)
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"],
                ["git", "-c", "user.name=t", "-c", "user.email=t@t.invalid", "commit", "-q", "-m", "start"]):
        subprocess.run(cmd, cwd=root, check=True, capture_output=True)
    net.forks._register("p", "P", "fork", root, {"kind": "fork", "origin": "scratch", "version": "game",
                                                  "platform": "pc", "ready": True}, "x")

    def report(self):
        units = json.loads((root / "config" / "game" / "functions.json").read_text())["units"]
        return {"units": [{"name": "u", "functions": [{"name": fn["name"], "size": str(fn["size"]),
                                                       "fuzzy_match_percent": 0.0} for fn in units["u"]]}]}
    monkeypatch.setattr(ForkProject, "report", report)
    monkeypatch.setattr(ForkProject, "units", lambda self: [Unit("u", None, None, "src/auto/u.cpp", False, None)])
    monkeypatch.setattr(ForkProject, "setup_commands", lambda self, version: [])
    monkeypatch.setattr(WindowsPC, "check_rebuild", lambda self, fp, version, log=print: (
        _delink(fp.root), scratch_pe.check_code(fp.root, version, log=log))[1])
    net.forks.sync("p", log=lambda *_: None)
    return net, root, [f"_func_{row['entry']:08X}" for row in rows[-len(EH_STUBS):]]


@pytest.mark.parametrize("level", [1, 3])
def test_a_project_made_before_is_brought_up_one_analysis_step_at_a_time(tmp_path, monkeypatch, level):
    """From a project made before PC projects had an analysis level (1) every step runs, each its own commit and
    feed line: the repairs, the stubs set aside, the pieces joined, its file of framed functions built with /Oy-, the
    rebuild identical after each. The joins never take a stub, though the first one reads the frame of the function
    before it, as a piece would; a project at 3 gets only the joins and the flags."""
    net, root, stubs = _project_made_before(tmp_path, monkeypatch, level)
    pid = net.project_row("p")["id"]
    consoles.get("pc").on_start(net, "p", log=lambda *_: None)
    said = [r["detail"] for r in net.store.all("SELECT detail FROM events WHERE project_id=? AND kind='analysis' "
                                               "ORDER BY id", pid)]
    expected = ["1 functions had a jump table", f"{len(EH_STUBS)} functions are exception-handling code",
                "1 pieces of functions Ghidra cut off",
                "1 source files whose functions keep an ebp frame build with /Oy-"][0 if level == 1 else 2:]
    assert len(said) == len(expected) and all(s.startswith(e) for s, e in zip(said, expected)), said
    log = subprocess.run(["git", "log", "--format=%s"], cwd=root, capture_output=True, text=True).stdout
    assert log.count("Analyse the game again") == len(said)
    manifest = json.loads((root / ".decomp.wtf.json").read_text())
    assert manifest["analysis"] == consoles.get("pc").ANALYSIS and manifest["rebuild"]["identical"]
    units = json.loads((root / "config" / "game" / "functions.json").read_text())["units"]["u"]
    assert [fn["name"] for fn in units if fn.get("generated")] == stubs  # every stub still its own, and set aside
    assert "_func_0040100F" not in {fn["name"] for fn in units}  # the piece, joined
    assert {r["name"] for r in net.store.all("SELECT name FROM functions WHERE project_id=? AND status='asm'",
                                              pid)} == set(stubs)
    note = net.store.one("SELECT text FROM notes WHERE project_id=?", pid)["text"]
    assert note.startswith("decompdecomp joined _func_0040100F to this function")
    consoles.get("pc").on_start(net, "p", log=lambda *_: None)  # (done: nothing new)
    assert net.store.one("SELECT COUNT(*) AS n FROM events WHERE project_id=? AND kind='analysis'", pid)["n"] == len(said)


def test_joins_that_break_the_rebuild_are_taken_back_without_notes(tmp_path, monkeypatch):
    from decompnet.consoles.pc import WindowsPC

    net, root, _ = _project_made_before(tmp_path, monkeypatch, 3)
    before = (root / "config" / "game" / "functions.json").read_text()
    monkeypatch.setattr(WindowsPC, "check_rebuild", lambda self, fp, version, log=print: {
        "identical": False, "detail": "1 functions rebuilt from the delinked objects differ from your copy"})
    consoles.get("pc").on_start(net, "p", log=lambda *_: None)
    pid = net.project_row("p")["id"]
    said = net.store.one("SELECT detail FROM events WHERE project_id=? AND kind='analysis'", pid)["detail"]
    assert said.startswith("a new analysis of the game (1 pieces") and "was taken back" in said
    assert (root / "config" / "game" / "functions.json").read_text() == before
    assert net.store.one("SELECT COUNT(*) AS n FROM notes WHERE project_id=?", pid)["n"] == 0
    assert json.loads((root / ".decomp.wtf.json").read_text())["analysis"] == consoles.get("pc").ANALYSIS


def _older_tools() -> dict[str, str]:
    """The template's tools as a project made before names.py paired constructors and destructors (and before
    configure.py read each file's compiler) has them, with the game's compiler and a flag the user added."""
    tools = {p.name: p.read_text() for p in (Path(scratch_pe.__file__).with_name("project_template") / "tools").glob("*.py")}
    tools["names.py"] = tools["names.py"].replace("??0", "?? 0")
    tools["configure.py"] = (tools["configure.py"].replace('MSVC_VERSION = "6.0"', 'MSVC_VERSION = "7.1"')
                             .replace('"/GX"]', '"/GX", "/Gr"]').replace("file_compilers", "no_map"))
    del tools["compile.py"]  # (a project of 1.0.3 had none)
    return tools


def test_a_project_made_before_gets_the_newest_build_tools_once(tmp_path, monkeypatch):
    """A PC project keeps the tools/ it was made with, so a newer names.py (constructors and destructors) never
    reached it: at start it gets the template's, with its own compiler and flags, and the map of each file's
    compiler, in one commit and feed line, kept as the rebuild check stays identical; agents' sources stay."""
    net, root, _ = _project_made_before(tmp_path, monkeypatch, consoles.get("pc").ANALYSIS, tools=_older_tools())
    source = (root / "src" / "auto" / "u.cpp").read_text()
    pc = consoles.get("pc")
    pc.on_start(net, "p", log=lambda *_: None)
    template = Path(scratch_pe.__file__).with_name("project_template") / "tools"
    assert (root / "tools" / "names.py").read_text() == (template / "names.py").read_text()
    assert (root / "tools" / "compile.py").read_text() == (template / "compile.py").read_text()
    configure = (root / "tools" / "configure.py").read_text()
    assert 'MSVC_VERSION = "7.1"' in configure and '"/GX", "/Gr"]' in configure and "file_compilers.json" in configure
    assert scratch_pe.compilers_file(root, "game").read_text() == "{}\n"
    assert (root / "src" / "auto" / "u.cpp").read_text() == source
    pid = net.project_row("p")["id"]
    said = [r["detail"] for r in net.store.all("SELECT detail FROM events WHERE project_id=? AND kind='analysis'", pid)]
    assert said == ["its build has the fixes decompdecomp's has had since the project was made (tools/compile.py, "
                    "tools/configure.py, tools/names.py, config/game/file_compilers.json, "
                    "config/game/file_flags.json)"]
    log = subprocess.run(["git", "log", "--format=%s"], cwd=root, capture_output=True, text=True).stdout
    assert log.count("Analyse the game again") == 1
    assert not subprocess.run(["git", "status", "--porcelain", "tools", "config"], cwd=root, capture_output=True,
                              text=True).stdout.strip()
    manifest = json.loads((root / ".decomp.wtf.json").read_text())
    assert manifest["tools"] == scratch_pe.tools_version() and manifest["rebuild"]["identical"]
    assert pc.file_compiler(ForkProject(root), "src/auto/u.cpp") == "MSVC/6.0"  # (the manifest's: try_compiler works)
    pc.on_start(net, "p", log=lambda *_: None)  # (done: nothing new)
    assert net.store.one("SELECT COUNT(*) AS n FROM events WHERE project_id=? AND kind='analysis'", pid)["n"] == 1


def test_build_tools_that_break_the_rebuild_are_taken_back_and_not_tried_again(tmp_path, monkeypatch):
    from decompnet.consoles.pc import WindowsPC

    net, root, _ = _project_made_before(tmp_path, monkeypatch, consoles.get("pc").ANALYSIS, tools=_older_tools())
    before = {p.name: p.read_text() for p in (root / "tools").iterdir()}
    checks = iter([{"identical": True, "detail": "identical"}, {"identical": False, "detail": "1 functions differ"}])
    monkeypatch.setattr(WindowsPC, "check_rebuild", lambda self, fp, version, log=print: next(checks))
    fp = ForkProject(root)
    fp.update_manifest(rebuild=WindowsPC.check_rebuild(None, fp, "game"))
    consoles.get("pc").on_start(net, "p", log=lambda *_: None)
    assert {p.name: p.read_text() for p in (root / "tools").iterdir()} == before
    assert not scratch_pe.compilers_file(root, "game").exists()
    pid = net.project_row("p")["id"]
    said = net.store.one("SELECT detail FROM events WHERE project_id=? AND kind='analysis'", pid)["detail"]
    assert said.startswith("a new analysis of the game (its build has the fixes") and "was taken back" in said
    assert json.loads((root / ".decomp.wtf.json").read_text())["tools"] == scratch_pe.tools_version()
    consoles.get("pc").on_start(net, "p", log=lambda *_: None)  # (tried for this version: not again)
    assert net.store.one("SELECT COUNT(*) AS n FROM events WHERE project_id=? AND kind='analysis'", pid)["n"] == 1


@needs_tools
def test_the_guess_for_the_demos_struct_is_the_struct_its_source_has(pc):
    """guess_struct reads Visual C++'s code: entity_update_physics's argument (on the stack) is the demo's Entity,
    field for field, from the functions that share it, as on MIPS (tests/test_structs.py). Two differences: the flags
    at +2 are only or'd and tested, which say nothing of their sign, and +0x18 (hp) is written only as an element of
    the global array, `[index + address]`, which isn't read as the array's type yet."""
    net, slug = pc
    syms = _map()
    name = next(s for s in syms if re.sub(r"^[_@]|@\d+$", "", s) == "entity_update_physics")
    name = name.replace("entity_update_physics", f"func_{syms[name]:08X}")
    text = net.guess_struct(slug, name, "arg0")
    guessed = {int(offset, 16): ctype for offset, ctype in re.findall(r"/\* 0x([0-9A-F]+) \*/ (\w+) unk_", text)}
    assert guessed == {0x00: "s16", 0x02: "s16", 0x04: "s32", 0x08: "s32", 0x0C: "s32", 0x10: "s32", 0x14: "s16",
                       0x16: "s16"}, text
    assert f"The same type: the global D_{syms['_g_entities']:08X}" in text  # the array entity_update's loop walks
    brief = net.get_function(name, slug)
    assert "### Likely struct layouts" in brief and f"- arg0: S_D_{syms['_g_entities']:08X}, +0x0 s16" in brief


def _registered(tmp_path, monkeypatch, program: bytes, rows: list[dict], slug: str = "p", net=None):
    """A hand-made PC project (_project: a source file per function) as the app has it: registered, measured from
    its functions.json (no compiler: nothing matches), its targets and rebuild check the delinked objects'."""
    from decompnet.app.engine import RomNetwork
    from decompnet.consoles.pc import WindowsPC
    from decompnet.forks.project import Unit

    root = _project(tmp_path / slug, program, rows)
    (root / "objdiff.json").write_text(json.dumps({"units": []}))
    (root / ".decomp.wtf.json").write_text(json.dumps({
        "created": "from scratch", "platform": "pc", "version": "game", "compiler": "MSVC/6.0",
        "game": {"path": "orig/game.exe", "sha1": hashlib.sha1(_tiny_exe(program)).hexdigest()},
        "analysis": consoles.get("pc").ANALYSIS, "tools": scratch_pe.tools_version()}))
    (root / ".gitignore").write_text("orig/\nbuild/\n")
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"],
                ["git", "-c", "user.name=t", "-c", "user.email=t@t.invalid", "commit", "-q", "-m", "start"]):
        subprocess.run(cmd, cwd=root, check=True, capture_output=True)
    net = net or RomNetwork(tmp_path / "decomp.db", tmp_path / "ws")
    net.forks._register(slug, slug.upper(), "fork", root, {"kind": "fork", "origin": "scratch", "version": "game",
                                                            "platform": "pc", "ready": True}, "x")

    def units(fp) -> dict:
        return json.loads((fp.root / "config" / "game" / "functions.json").read_text())["units"]
    monkeypatch.setattr(ForkProject, "report", lambda self: {"units": [
        {"name": u, "functions": [{"name": fn["name"], "size": str(fn["size"]), "fuzzy_match_percent": 0.0} for fn in fns]}
        for u, fns in units(self).items()]})
    unit = lambda self, u: Unit(u, self.root / "build" / "game" / "target" / f"{u}.obj", None,  # noqa: E731
                                f"src/auto/{u}.cpp", False, None)
    monkeypatch.setattr(ForkProject, "units", lambda self: [unit(self, u) for u in units(self)])
    monkeypatch.setattr(ForkProject, "unit", lambda self, u: unit(self, u) if u in units(self) else
                        (_ for _ in ()).throw(ForkError(f"no unit {u!r}")))
    monkeypatch.setattr(ForkProject, "setup_commands", lambda self, version: [])
    monkeypatch.setattr(WindowsPC, "check_rebuild", lambda self, fp, version, log=print: (
        _delink(fp.root), scratch_pe.check_code(fp.root, version, log=log))[1])
    net.forks.sync(slug, log=lambda *_: None)
    return net, root


# Ghidra took two functions for one (0x401000), and a function with data after its return for all code (0x401020)
MERGED = FRAME_RET * 2 + b"\x90" * 14 + FRAME_RET + bytes(range(1, 8)) + FRAME_RET
MERGED_ROWS = [_row(0x401000, 0x401012), _row(0x401020, 0x401030), _row(0x401030, 0x401039)]


def test_agents_of_two_models_end_a_function_where_it_really_ends(tmp_path, monkeypatch):
    """report_boundary on PC: three agents of two models say where a function ends; the rest becomes a function of
    its own (code: open to agents; data: set aside), checked by the rebuild, with a note; one that can't hold is
    refused and remembered."""
    net, root = _registered(tmp_path, monkeypatch, MERGED, MERGED_ROWS)
    say = lambda who, fn, end, why="a second function starts there: push ebp": net.request_boundary(  # noqa: E731
        net.local_agent(who)[0], "p", fn, end, why)
    for end in ("+0x30", "0x00401000", "hello"):
        with pytest.raises(NetworkError, match="an address inside it"):
            say("claude-opus-5-5-1", "_func_00401000", end)
    assert "1 of 3 agents" in say("opencode-beam-free-1", "_func_00401000", "+9")
    assert "1 of 2 models" in say("opencode-beam-free-2", "_func_00401000", "0x00401009")  # (the same address)
    said = say("claude-sonnet-5-5-1", "_func_00401000", "+9")
    assert said.startswith("With yours, 3 agents of 2 models agree: _func_00401000 ends at 0x00401009; the 23 bytes "
                           "after it are _func_00401009, a function of its own"), said
    rows = {r["name"]: r for r in net.store.all("SELECT * FROM functions WHERE project_id=?", net.project_row("p")["id"])}
    assert rows["_func_00401000"]["size"] == 9 and rows["_func_00401009"]["status"] == "open"
    assert json.loads((root / ".decomp.wtf.json").read_text())["rebuild"]["identical"]
    assert "decompdecomp cut it" in net.store.one("SELECT text FROM notes WHERE function_id=?",
                                                  rows["_func_00401000"]["id"])["text"]
    data = "data after its return: 01 02 03 04 isn't code"
    for who in ("opencode-beam-free-1", "opencode-beam-free-2", "claude-sonnet-5-5-1"):
        said = say(who, "_func_00401020", "+9", data)
    assert said.endswith("_func_00401029, set aside as not code. The game still builds byte for byte."), said
    tail = net._function("_func_00401029", "p")
    assert tail["status"] == "asm" and data in json.loads(tail["meta"])["asm_code"]
    for who in ("opencode-beam-free-1", "opencode-beam-free-2"):  # cutting off the padding Visual C++ puts there
        say(who, "_func_00401009", "+9", "nops")
    assert "refused: what follows 0x00401012 is padding" in say("claude-sonnet-5-5-1", "_func_00401009", "+9", "nops")
    assert "was tried, and refused" in say("claude-opus-5-5-1", "_func_00401009", "+9", "nops")
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched' WHERE name='_func_00401030'")
    with pytest.raises(NetworkError, match="its end is proven"):
        say("claude-opus-5-5-1", "_func_00401030", "+3")


def _calls(at: int, target: int, count: int = 6) -> bytes:
    """A function at `at` with a frame that calls `target` `count` times: push ebp; mov ebp, esp; call...; pop; ret."""
    code = bytearray(PUSH_FRAME)
    for _ in range(count):
        code += b"\xe8" + struct.pack("<i", target - (at + len(code) + 5))
    return bytes(code + bytes.fromhex("5dc3"))


# the same function (it calls its neighbour six times) in two programs, at other addresses, calling other addresses
GAME_A = _calls(0x401000, 0x401030).ljust(0x30, b"\x90") + FRAME_RET
GAME_B = FRAME_RET.ljust(0x10, b"\x90") + _calls(0x401010, 0x401040).ljust(0x30, b"\x90") + FRAME_RET
ROWS_A = [_row(0x401000, 0x401023), _row(0x401030, 0x401039)]
ROWS_B = [_row(0x401000, 0x401009), _row(0x401010, 0x401033), _row(0x401040, 0x401049)]


def test_a_pc_projects_targets_have_twins_by_their_masked_code():
    """A PC project's targets are COFF objects, which twins read as nothing (pyelftools): no function of a PC game
    had a twin, in it or across games. Identical code with other addresses is one fingerprint; its relocations name
    what C writes (func_00401030, not _func_00401030)."""
    from decompnet.app import twins

    prints = []
    for program, rows in ((GAME_A, ROWS_A), (GAME_B, ROWS_B)):
        pe = scratch_pe.read_pe(_tiny_exe(program))
        entries = [scratch_pe._entry(pe, f) for f in scratch_pe.analyse(pe, rows)]
        prints.append(twins.object_prints(_template_tool("delink").coff(entries, _tiny_exe(program))))
    a, b = prints[0]["_func_00401000"], prints[1]["_func_00401010"]
    assert a.key == b.key and a.key != prints[1]["_func_00401000"].key
    assert (a.name, a.refs[0], b.refs[0]) == ("func_00401000", "func_00401030", "func_00401040")
    assert twins.renames(a, b, a.name, b.name) == {"func_00401000": "func_00401010", "func_00401030": "func_00401040"}


def test_code_matched_in_one_pc_game_is_tried_on_its_twin_in_another(tmp_path, monkeypatch):
    """Twins across your games, in project mode: a function matched in one PC project, identical in another (a C
    runtime both link), is tried there with that project's names; the judge decides. Only where both files are
    built with the same Visual C++."""
    from decompnet.app.forkengine import ForkEngine

    net, root_a = _registered(tmp_path, monkeypatch, GAME_A, ROWS_A, slug="a")
    _, root_b = _registered(tmp_path, monkeypatch, GAME_B, ROWS_B, slug="b", net=net)
    (root_a / "src" / "auto" / "text_00401000.cpp").write_text(
        '#include "common.h"\nvoid func_00401030(void);\n\nvoid func_00401000(void) {\n'
        + "    func_00401030();\n" * 6 + "}\n")
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched' WHERE name='_func_00401000' AND project_id=?",
                   (net.project_row("a")["id"],))
    tried = []
    monkeypatch.setattr(ForkEngine, "attempt", lambda self, agent, slug, function, source, keep=True, **kw: (
        tried.append((slug, function, source)), {"matched": True, "score": 0})[1])
    scratch_pe.compilers_file(root_a, "game").write_text("{}")  # (both built with the project's own, 6.0)
    scratch_pe.compilers_file(root_b, "game").write_text(json.dumps({"src/auto/text_00401010.cpp": "7.1"}))
    assert net.twins.across_games("b") == 0 and not tried  # (another Visual C++ builds that file)
    scratch_pe.compilers_file(root_b, "game").write_text("{}")
    assert net.twins.across_games("b") == 1
    (slug, function, source), = tried
    assert (slug, function) == ("b", "_func_00401010")
    assert source.endswith("void func_00401040(void);\nvoid func_00401010(void) {\n" + "    func_00401040();\n" * 6 + "}\n")
    assert "identical to _func_00401000 in A (matched)" in source


FIVE = FRAME_RET.ljust(0x10, b"\x90") * 5  # five functions, a source file each (_project), 6.0 the project's compiler
FIVE_ROWS = [_row(0x401000 + 0x10 * k, 0x401009 + 0x10 * k) for k in range(5)]


def _file_compiler_project(tmp_path, monkeypatch, scores: dict[tuple[str, str], tuple[dict, dict]]):
    """A PC project of five files whose builds are `scores` ((file, compiler) -> its functions' match % with the
    file's own compiler and with that one), its Rich header naming 6.0 and 7.1, proven changes applying by
    themselves, and every file switch recorded instead of built."""
    from decompnet.app.forkengine import ForkEngine
    from decompnet.consoles.pc import WindowsPC

    net, root = _registered(tmp_path, monkeypatch, FIVE, FIVE_ROWS)
    scratch_pe.compilers_file(root, "game").write_text("{}")
    net.filecompilers.background = False
    net.set_auto_adjust("p", True)
    monkeypatch.setattr(WindowsPC, "program_compilers", lambda self, fp: [
        ("MSVC/6.0", 148, "Visual C++ 6.0 SP5 (build 8966)", True), ("MSVC/7.1", 30, "Visual C++ .NET 2003 (7.1, build 3077)", True)])
    monkeypatch.setattr(ForkEngine, "file_scores", lambda self, slug, source, compiler: scores.get(
        (source.removeprefix("src/auto/text_").removesuffix(".cpp"), compiler), ({}, {})))
    switched = []
    monkeypatch.setattr(ForkEngine, "set_compiler_ranges", lambda self, slug, ranges, compiler: switched.append(
        (ranges, compiler)) or "switched")
    return net, root, switched


def _only_with(name: str, compiler: str = "MSVC/7.1", own: float = 50.0) -> dict:
    return {(name[-8:], compiler): ({f"_func_{name[-8:]}": own}, {f"_func_{name[-8:]}": 100.0})}


def test_the_survey_confirms_another_visual_cpp_and_its_neighbouring_file_applies_it(tmp_path, monkeypatch):
    """The compiler map on PC: the Rich header's compilers, the survey building each file with them (a function that
    matches with 7.1 and not with its file's 6.0 confirms 7.1), the map's run becoming a suggestion, and the range
    check applying it once the next file confirms it too, stopping at a file whose match needs 6.0."""
    scores = {**_only_with("00401020"), **_only_with("00401030"),
              ("00401010", "MSVC/7.1"): ({"_func_00401010": 100.0}, {"_func_00401010": 60.0})}  # needs its 6.0
    net, root, switched = _file_compiler_project(tmp_path, monkeypatch, scores)
    text = net.compiler_map_text("p")
    assert "Visual C++ 6.0 SP5 (build 8966): 148 files; Visual C++ .NET 2003 (7.1, build 3077): 30 files" in text
    assert "not run yet" in text
    assert net.filecompilers.survey("p")["tried"] == 0  # (no C for any function yet: nothing to build)
    with net.store.tx() as db:  # agents' attempts
        db.execute("UPDATE functions SET best_source='int f(void);' WHERE project_id=?", (net.project_row("p")["id"],))
    # (only the first file's builds this time: the survey goes on where it stopped)
    assert net.filecompilers.survey("p", limit=3)["confirmed"] == 1
    survey = net.cfg(net.project_row("p"))["file_survey"]
    assert survey["confirmed"] == {"_func_00401020": "MSVC/7.1"}
    (check,) = net.cfg(net.project_row("p"))["file_checks"]
    assert check["status"] == "confirmed" and check["origin"] == "src/auto/text_00401020.cpp"
    assert check["evidence"]["src/auto/text_00401010.cpp"] == "refuted"
    assert check["evidence"]["src/auto/text_00401030.cpp"] == "confirmed"
    assert switched == [([[0x401020, 0x401040]], "MSVC/7.1")]
    (suggestion,) = net.cfg(net.project_row("p"))["suggestions"]
    assert suggestion["status"] == "applied" and suggestion["ranges"] == [[0x401020, 0x401040]]
    text = net.compiler_map_text("p")
    assert "1 functions confirmed in 3 files" in text and "#1 0x401020-0x401030 Visual C++ 7.1: confirmed by" in text
    answer = net.compiler_answer("p")
    assert answer["checks"][0]["status"] == "confirmed" and answer["survey"]["confirmed"] == 1


def test_a_suggested_file_compiler_both_neighbours_refute_is_a_quirk(tmp_path, monkeypatch):
    """An agent's suggestion for a file whose function matches with 7.1, between two files whose matches need 6.0:
    dismissed, with a note on the function to find C that matches with its file's compiler."""
    refutes = lambda name: {(name, "MSVC/7.1"): ({f"_func_{name}": 100.0}, {})}  # noqa: E731  (doesn't build with it)
    scores = {**_only_with("00401020"), **refutes("00401010"), **refutes("00401030")}
    net, root, switched = _file_compiler_project(tmp_path, monkeypatch, scores)
    agent = net.local_agent("claude-sonnet-5-5-1")[0]
    said = net.suggest(agent, "p", "7.1 built this file", compiler="MSVC/7.1", function="_func_00401020")
    assert said.startswith("Suggested. Range check #1 started")
    (check,) = net.cfg(net.project_row("p"))["file_checks"]
    assert check["status"] == "quirk" and not switched
    assert net.cfg(net.project_row("p"))["suggestions"][0]["status"] == "dismissed"
    note = net.store.one("SELECT text FROM notes WHERE function_id=?", net._function("_func_00401020", "p")["id"])
    assert "Range check #1" in note["text"] and "matches with Visual C++ 7.1" in note["text"]


def test_a_neighbouring_file_nobody_can_tell_yet_goes_to_an_agent(tmp_path, monkeypatch):
    """Where no file settles a check, the nearest file without evidence is the next agent's task (first in line, a
    note saying to try_compiler); a try_compiler that switches it confirms the check."""
    net, root, switched = _file_compiler_project(tmp_path, monkeypatch, _only_with("00401020"))
    agent = net.local_agent("claude-sonnet-5-5-1")[0]
    net.suggest(agent, "p", "7.1 built these", compiler="MSVC/7.1", ranges="0x401020-0x401030")
    (check,) = net.cfg(net.project_row("p"))["file_checks"]
    assert check["status"] == "checking" and check["said"].startswith("checking: _func_00401010 in src/auto/text_00401010")
    assert net.cfg(net.project_row("p"))["priority"] == ["_func_00401010"]
    note = net.store.one("SELECT text FROM notes WHERE function_id=?", net._function("_func_00401010", "p")["id"])
    assert 'try_compiler("_func_00401010", "MSVC/7.1")' in note["text"]
    net.filecompilers.tried("p", "src/auto/text_00401010.cpp", "MSVC/7.1")  # (what try_compiler's switch says)
    (check,) = net.cfg(net.project_row("p"))["file_checks"]
    assert check["status"] == "confirmed" and switched == [([[0x401010, 0x401030]], "MSVC/7.1")]
    assert net.cfg(net.project_row("p"))["priority"] == []


@needs_tools
def test_a_file_built_with_another_visual_cpp_keeps_its_own_and_says_which_matches_need_it(pc):
    """The survey's and range checks' evidence, built for real: the file holding player_take_damage (matched with
    6.0, otherwise with 7.1) built with 7.1 refutes 7.1 for it, and the file is its own compiler's again after."""
    net, slug = pc
    root = Path(net.project_row(slug)["root"])
    name = next(r["name"] for r in net.store.all("SELECT name FROM functions WHERE project_id=? AND status='matched'",
                                                 net.project_row(slug)["id"]) if f"{_map()['_player_take_damage']:08X}" in r["name"])
    meta = json.loads(net._function(name, slug)["meta"])
    source, name = meta["source"], meta["symbol"]  # (the report's name: the row's can have a ' after it)
    before = scratch_pe.file_compilers(root, "demoquest")
    mine, theirs = net.forks.file_scores(slug, source, "MSVC/7.1")
    assert mine[name] == 100 and theirs.get(name, 0) < 100
    assert net.filecompilers.verdict(slug, source, "MSVC/7.1") == "refuted"
    assert scratch_pe.file_compilers(root, "demoquest") == before
    assert net.forks.file_scores(slug, source, "MSVC/6.0")[0][name] == 100  # (built with its own again)
    # the demo's Rich header names 6.0 only, and every file is 6.0's again (the test before): nothing else to try
    assert "the survey has no other compiler to try" in net.compiler_map_text(slug)


def test_a_pc_project_is_reimported_with_a_new_analysis_and_keeps_its_matches(tmp_path, monkeypatch):
    """Reimport for PC (Settings): Ghidra again, read by the newest rules; a function the old analysis merged is two
    now, each in the source file that held its code, the matched function keeps its extent whatever the new analysis
    says there, the code still rebuilds byte for byte; another program is refused."""
    net, root = _registered(tmp_path, monkeypatch, MERGED, MERGED_ROWS)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched' WHERE name='_func_00401030'")
    rows = [_row(0x401000, 0x401009), _row(0x401009, 0x401012), _row(0x401020, 0x401030), _row(0x401030, 0x401033)]
    monkeypatch.setattr(ghidra, "functions", lambda program, timeout=3600: rows)
    with pytest.raises(NetworkError, match="isn't this project's program"):
        net.forks.reimport("p", ROOT / "README.md")
    said = net.forks.reimport("p", root / "orig" / "game.exe", log=lambda *_: None)
    assert said["said"] == "4 functions (it had 3), 2 of them new or with another extent; its 1 matched functions kept"
    units = json.loads((root / "config" / "game" / "functions.json").read_text())["units"]
    assert {u: [(f["name"], f["size"]) for f in fns] for u, fns in units.items()} == {
        "text_00401000": [("_func_00401000", 9), ("_func_00401009", 0x17)], "text_00401020": [("_func_00401020", 0x10)],
        "text_00401030": [("_func_00401030", 9)]}
    assert json.loads((root / ".decomp.wtf.json").read_text())["rebuild"]["identical"]
    assert said["functions"] == 4 and "Analyse the game again" in subprocess.run(
        ["git", "log", "--format=%s"], cwd=root, capture_output=True, text=True).stdout
    assert net.forks.overview("p")["reimports"]
    assert net.forks.reimport("p", root / "orig" / "game.exe", log=lambda *_: None)["said"].startswith("the new analysis "
                                                                                                    "changed nothing")


# ---------------------------------------------------------------------------------------------------- the game's DLLs
YEAR = 365 * 24 * 3600
BUILT = 1_017_000_000  # (2002: a build's time)


def _tiny_program(code: bytes, base: int, *, dll: bool = True, timestamp: int = BUILT, exports: dict | None = None,
                  relocs: tuple[int, ...] = (), stripped: bool = False) -> bytes:
    """A 32-bit Windows program (a DLL by default) whose .text, at base + 0x1000, is `code`; its export table
    (`exports`: offset in .text -> name, or a forwarder's "dll.name" as the value of a negative offset) and base
    relocations (`relocs`: offsets in .text of the addresses in it) in sections of their own."""
    text_rva, edata_rva, reloc_rva, raw = 0x1000, 0x2000, 0x3000, 0x200
    edata = bytearray(40)
    named = sorted((exports or {}).items(), key=lambda kv: kv[1])
    functions = edata_rva + 40
    names_at = functions + 4 * len(named)
    ordinals = names_at + 4 * len(named)
    strings = ordinals + 2 * len(named)
    struct.pack_into("<IIHHIIIIIII", edata, 0, 0, 0, 0, 0, 0, 1, len(named), len(named), functions, names_at, ordinals)
    table, pointers, numbers, text = bytearray(), bytearray(), bytearray(), bytearray()
    for k, (offset, name) in enumerate(named):
        name, forward = (name, None) if offset >= 0 else (name.split(".")[1], name)
        target = text_rva + offset if forward is None else strings + len(text)
        if forward is not None:
            text += forward.encode() + b"\0"
        table += struct.pack("<I", target)
        numbers += struct.pack("<H", k)
        pointers += struct.pack("<I", strings + len(text))
        text += name.encode() + b"\0"
    edata += table + pointers + numbers + text
    block = struct.pack("<II", text_rva, 8 + 2 * len(relocs) + 2 * (len(relocs) % 2)) + b"".join(
        struct.pack("<H", 0x3000 | r) for r in relocs) + b"\0\0" * (len(relocs) % 2)
    bodies = [code, bytes(edata), block]
    head = bytearray(raw)
    head[:2], head[0x3C:0x40] = b"MZ", struct.pack("<I", 0x40)
    flags = 0x0102 | (0x2000 if dll else 0) | (0x0001 if stripped else 0)
    struct.pack_into("<4sHHIIIHH", head, 0x40, b"PE\0\0", 0x14C, 3, timestamp, 0, 0, 0xE0, flags)
    opt = 0x58
    struct.pack_into("<H", head, opt, 0x10B)
    struct.pack_into("<I", head, opt + 16, text_rva)
    struct.pack_into("<I", head, opt + 28, base)
    struct.pack_into("<I", head, opt + 56, 0x4000)
    struct.pack_into("<II", head, opt + 96, edata_rva if named else 0, len(edata) if named else 0)
    struct.pack_into("<II", head, opt + 136, reloc_rva if relocs else 0, len(block) if relocs else 0)
    out, at = bytearray(), raw
    for k, (name, rva, body, kind) in enumerate(zip((b".text", b".edata", b".reloc"), (text_rva, edata_rva, reloc_rva),
                                                   bodies, (0x60000020, 0x40000040, 0x42000040))):
        size = -(-len(body) // raw) * raw
        struct.pack_into("<8sIIIIIIHHI", head, opt + 0xE0 + 40 * k, name, len(body), rva, size, at, 0, 0, 0, 0, kind)
        out += body.ljust(size, b"\0")
        at += size
    return bytes(head) + bytes(out)


# 0x10001000 reads the word at 0x10001010 (its own data: mov eax, [0x10001010]; ret), 0x10001010 returns the address
# of 0x10001000 (mov eax, 0x10001000; ret): both relocated, so moving the DLL changes both
DLL_CODE = bytes.fromhex("a110100010c3").ljust(0x10, b"\x90") + bytes.fromhex("b800100010c3").ljust(0x10, b"\x90")
DLL_RELOCS = (0x1, 0x11)


def _game_folder(tmp_path: Path) -> Path:
    """A game's folder: its program, two DLLs of its own that both want 0x10000000, and the others' DLLs games carry."""
    top = tmp_path / "game"
    (top / "data").mkdir(parents=True)
    exe = _tiny_program(bytes.fromhex("33c0c3"), 0x400000, dll=False)
    (top / "game.exe").write_bytes(exe)
    engine = _tiny_program(DLL_CODE, 0x10000000, exports={0: "_Engine_Init@4", 0x10: "Engine_Data"}, relocs=DLL_RELOCS)
    (top / "engine.dll").write_bytes(engine)
    (top / "data" / "logic.dll").write_bytes(_tiny_program(DLL_CODE, 0x10000000, exports={0: "Engine_Init"},
                                                           relocs=DLL_RELOCS))
    for name in ("msvcr71.dll", "d3d8.dll", "binkw32.dll", "avcodec-53.dll", "drvmgt.dll", "widescreen.asi"):
        (top / name).write_bytes(engine)
    (top / "patch.dll").write_bytes(_tiny_program(DLL_CODE, 0x10000000, relocs=DLL_RELOCS, timestamp=BUILT + 3 * YEAR))
    (top / "fixed.dll").write_bytes(_tiny_program(DLL_CODE, 0x10000000, stripped=True))
    (top / "notes.dll").write_bytes(b"not a program")
    for folder in ("launcher", ".cache", "a/b/c"):  # a program's own folder, a hidden one, one too deep: not looked in
        (top / folder).mkdir(parents=True)
        (top / folder / "hook.dll").write_bytes(engine)
    (top / "launcher" / "launcher.exe").write_bytes(exe)
    return top


def test_the_games_own_dlls_are_found_and_the_others_left_out_with_why(tmp_path):
    """#12: many games keep much of their code in DLLs of their own. The import takes those beside the .exe (its
    folder and two below), each at its own address unless the program or a DLL before it has that one; the C
    runtime, Windows' own names (a wrapper's ddraw.dll), middleware, copy protection, ASI plugins and DLLs built long
    after the program (a mod's) are left out, and the log says which and why."""
    top = _game_folder(tmp_path)
    main = scratch_pe.read_pe((top / "game.exe").read_bytes())
    found, left_out = scratch_pe.find_modules(top / "game.exe", main)
    assert [(m.rel, m.name, hex(m.pe.image_base), hex(m.preferred)) for m in found] == [
        ("engine.dll", "engine", "0x10000000", "0x10000000"), ("data/logic.dll", "logic", "0x10010000", "0x10000000")]
    assert dict(left_out) == {"avcodec-53.dll": scratch_pe.MIDDLEWARE, "binkw32.dll": scratch_pe.MIDDLEWARE,
                              "d3d8.dll": scratch_pe.STAND_IN,
                              "drvmgt.dll": scratch_pe.PROTECTION, "fixed.dll": scratch_pe.NO_ROOM,
                              "msvcr71.dll": scratch_pe.RUNTIME, "notes.dll": scratch_pe.NOT_PROGRAM,
                              "patch.dll": scratch_pe.LATER, "widescreen.asi": scratch_pe.PLUGIN}
    lines = scratch_pe.left_out_lines(left_out)
    assert len(lines) == 8 and any(line.startswith("left out, ASI plugins") and line.endswith("widescreen.asi")
                                   for line in lines)
    # moved as Windows moves it: every address its base relocations name, and its header's base
    logic = found[1].pe
    assert logic.read(0x10011000, 6) == bytes.fromhex("a110100110c3")
    assert logic.read(0x10011010, 6) == bytes.fromhex("b800100110c3")
    assert scratch_pe.rebase(logic.data, 0x10000000) == (top / "data" / "logic.dll").read_bytes()
    # the names it exports, as C names them
    assert scratch_pe.exports(found[0].pe) == {0x10001000: "Engine_Init", 0x10001010: "Engine_Data"}


def test_a_dlls_exports_are_named_as_c_and_cpp_write_them():
    for raw, name in (("Init", "Init"), ("_Init@4", "Init"), ("@Mix@8", "Mix"), ("?start@App@@QAEXXZ", "App::start"),
                      ("?start@UI@Game@@QAEXXZ", "Game::UI::start"), ("?init@@YAXXZ", "init"),
                      ("??0App@@QAE@XZ", None), ("?f@?$Vec@H@@QAEXXZ", None), ("Init@@", None)):
        assert scratch_pe.export_name(raw) == name, raw


def test_a_forwarded_export_is_another_dlls_code():
    dll = _tiny_program(DLL_CODE, 0x10000000, exports={0: "Init", -1: "kernel32.Sleep"})
    assert scratch_pe.exports(scratch_pe.read_pe(dll)) == {0x10001000: "Init"}


def test_a_folder_imports_its_biggest_program_decompdecomp_can_start(tmp_path):
    """Pointing at a game's folder: its .exe is the biggest program decompdecomp can start (a launcher or an
    uninstaller is smaller; a 64-bit tool can't start), and the GUI knows a PC game's DLLs come with its path, not a
    dropped file."""
    top = _game_folder(tmp_path)
    (top / "setup.exe").write_bytes(_tiny_program(bytes.fromhex("c3"), 0x400000, dll=False)[:0x400])
    (top / "tool64.exe").write_bytes(_windows_program(machine=0x8664) + bytes(0x10000))
    assert consoles.game_in(top) == top / "game.exe"
    with pytest.raises(Exception, match="holds no game file"):
        consoles.game_in(top / "data")
    assert consoles.get("pc").describe()["games_in_folders"] and not consoles.get("n64").describe()["games_in_folders"]


def test_importing_a_folder_starts_from_its_program(tmp_path, monkeypatch):
    from decompnet.app.engine import RomNetwork

    top = _game_folder(tmp_path)
    started = []
    monkeypatch.setattr(type(consoles.get("pc")), "import_game", lambda self, net, file, **_: started.append(file) or "s")
    said = []
    net = RomNetwork(tmp_path / "app.db", tmp_path / "work")
    assert net.import_rom(top, confirmed=True, log=said.append) == "s"
    assert started == [top / "game.exe"] and said == ["the game's program in that folder: game.exe"]


def _project_with_dlls(tmp_path: Path) -> Path:
    """A PC project of the game folder's program and its two DLLs, as create writes it (no Ghidra, no compiler), and
    its objects delinked by the project's own tools/delink.py."""
    top = _game_folder(tmp_path)
    root = tmp_path / "proj"
    main = scratch_pe.read_pe((top / "game.exe").read_bytes())
    found, _ = scratch_pe.find_modules(top / "game.exe", main)
    (root / "orig" / "data").mkdir(parents=True)
    shutil.copy(top / "game.exe", root / "orig" / "game.exe")
    units = {"text_00401000": [scratch_pe._entry(main, f) for f in scratch_pe.analyse(main, [_row(0x401000, 0x401003)])]}
    modules = []
    for m in found:
        shutil.copy(m.path, root / "orig" / m.rel)
        base = m.pe.image_base
        fns = scratch_pe.analyse(m.pe, [_row(base + 0x1000, base + 0x1006), _row(base + 0x1010, base + 0x1016)])
        units[f"{m.name}/text_{base + 0x1000:08X}"] = [scratch_pe._entry(m.pe, f) for f in fns]
        modules.append({"name": m.name, "exe": f"orig/{m.rel}", "sha1": hashlib.sha1(m.path.read_bytes()).hexdigest(),
                        "image_base": base, "preferred_base": m.preferred})
    info = {"exe": "orig/game.exe", "sha1": hashlib.sha1((top / "game.exe").read_bytes()).hexdigest(),
            "image_base": 0x400000, "modules": modules, "units": units}
    (root / "config" / "game").mkdir(parents=True)
    (root / "config" / "game" / "functions.json").write_text(json.dumps(info))
    for unit in units:
        (root / "src" / "auto" / f"{unit}.cpp").parent.mkdir(parents=True, exist_ok=True)
        (root / "src" / "auto" / f"{unit}.cpp").write_text('#include "common.h"\n')
    shutil.copytree(Path(scratch_pe.__file__).with_name("project_template") / "tools", root / "tools")
    subprocess.run([sys.executable, "tools/delink.py", "config/game/functions.json", "build/game/target"], cwd=root,
                   check=True)
    return root


def test_a_projects_dlls_are_delinked_at_their_addresses_and_rebuild_byte_for_byte(tmp_path):
    """Each DLL's functions are cut out of it at the address the project gives it (the moved one moved by delink.py
    as by the import), refer to its own code and data by func_/D_<address> (no two programs share one), and the
    rebuild check puts each program's code back together against that program."""
    root = _project_with_dlls(tmp_path)
    info = json.loads((root / "config" / "game" / "functions.json").read_text())
    logic = info["units"]["logic/text_10011000"]
    assert [fn["name"] for fn in logic] == ["_func_10011000", "_func_10011010"]
    assert logic[0]["relocs"] == [[1, scratch_pe.DIR32, "_func_10011010", 0]]
    assert logic[1]["relocs"] == [[1, scratch_pe.DIR32, "_func_10011000", 0]]
    assert (root / "build" / "game" / "target" / "logic" / "text_10011000.obj").is_file()
    result = scratch_pe.check_code(root, "game", log=lambda *_: None)
    assert result["identical"], result
    assert result["detail"].endswith("by program: game.exe 1, engine.dll 2, logic.dll 2")
    # an object delinked from the DLL at its preferred address instead differs: the check reads each program
    coff = _template_tool("delink").coff
    moved = root / "build" / "game" / "target" / "logic" / "text_10011000.obj"
    moved.write_bytes(coff([{**logic[0], "relocs": []}], (root / "orig" / "data" / "logic.dll").read_bytes()))
    assert not scratch_pe.check_code(root, "game", log=lambda *_: None)["identical"]


def test_a_dlls_thunk_for_an_import_the_program_has_too_goes_by_its_address():
    """The .exe and a DLL both call RtlUnwind through a thunk of their own, each analysed as _RtlUnwind@16: one symbol
    for two addresses would rebuild every call to it at one of them (a Visual C++ 6.0 game with a DLL of its own). The
    later program's thunk goes by its address, and its calls with it."""
    F = scratch_pe.Function
    thunk, caller = F(0x10001000, 6, "_RtlUnwind@16"), F(0x10001010, 9, "_func_10001010", [(1, 20, "_RtlUnwind@16", 0)])
    taken = {"_RtlUnwind@16", "_func_00401000"}
    scratch_pe.own_symbols([thunk, caller], taken)
    assert (thunk.name, caller.relocs) == ("_func_10001000@16", [(1, 20, "_func_10001000@16", 0)])
    assert {"_func_10001000@16", "_func_10001010"} <= taken


def test_a_moved_dll_is_rebuilt_at_its_own_address_and_reanalysed_on_its_own(tmp_path):
    """`ninja rebuild` writes each DLL where orig/ has it, back at the address its header prefers, byte for byte the
    user's copy (the project moved it); Reimport analyses each program again by itself: a DLL's new analysis changes
    its functions only, and its new files stay in its folder."""
    root = _project_with_dlls(tmp_path)
    report = _template_tool("rebuild").rebuild(Path("config/game/functions.json"), Path("build/game"), root)
    assert report["identical"] and set(report["programs"]) == {"", "engine", "logic"}
    assert (root / "build" / "game" / "data" / "logic.dll").read_bytes() == (root / "orig" / "data" / "logic.dll").read_bytes()
    assert report["programs"]["logic"]["output"] == "build/game/data/logic.dll"
    before = json.loads((root / "config" / "game" / "functions.json").read_text())["units"]
    base = 0x10010000
    # Ghidra took logic.dll's two functions for one this time
    said = scratch_pe.reanalyse_program(root, "game", {"logic": [_row(base + 0x1000, base + 0x1016)]})
    assert said == "4 functions (it had 5), 1 of them new or with another extent; its 0 matched functions kept"
    units = json.loads((root / "config" / "game" / "functions.json").read_text())["units"]
    assert {u: fns for u, fns in units.items() if not u.startswith("logic/")} == \
        {u: fns for u, fns in before.items() if not u.startswith("logic/")}
    assert [(fn["name"], fn["size"]) for fn in units["logic/text_10011000"]] == [("_func_10011000", 0x16)]
    assert scratch_pe.reanalyse_program(root, "game", {"engine": [_row(0x10001000, 0x10001006),
                                                                  _row(0x10001010, 0x10001016)]}) is None


def test_a_dlls_data_functions_and_files_are_its_own(tmp_path):
    """read_data and the diff's values read the program an address is in; joins stay within one program; a DLL's new
    source file stays in its folder; its classes come from its own RTTI (none here) without failing."""
    root = _project_with_dlls(tmp_path)
    manifest = {"version": "game", "game": {"path": "orig/game.exe"}}
    assert scratch_pe.data_bytes(root, manifest, 0x10011000, 6) == bytes.fromhex("a110100110c3")
    assert scratch_pe.data_bytes(root, manifest, 0x401000, 3) == bytes.fromhex("33c0c3")
    assert "a1 10 10 01 10 c3" in scratch_pe.read_data(root, manifest, "D_10011000", 8)
    with pytest.raises(ForkError, match="or its DLLs"):
        scratch_pe.read_data(root, manifest, "D_20000000", 8)
    with pytest.raises(ForkError, match="two programs"):
        scratch_pe.join_functions(root, "game", "_func_10001010", "_func_10011000")
    with pytest.raises(ForkError, match="src/auto/logic/"):
        scratch_pe.split_unit(root, "game", "src/auto/logic/text_10011000.cpp", "_func_10011010", "src/auto/b.cpp")
    scratch_pe.split_unit(root, "game", "src/auto/logic/text_10011000.cpp", "_func_10011010", "src/auto/logic/b.cpp")
    assert "logic/b" in json.loads((root / "config" / "game" / "functions.json").read_text())["units"]
    assert scratch_pe.mark_exception_stubs(root, "game") == 0
    from decompnet.consoles.pc import rtti

    assert rtti.for_project(root, manifest).by_vtable == {}
    (root / "orig" / "engine.dll").unlink()  # a DLL not in the project (yet): what reads every program waits
    assert scratch_pe.missing(root, "game") == ["orig/engine.dll"] and scratch_pe.mark_exception_stubs(root, "game") is None


@needs_tools
@pytest.mark.skipif(toolchains.installed("msvc2005") is None, reason="the demo's DLL is built with Visual C++ 2010 SP1")
def test_a_game_folder_brings_its_dll_and_agents_match_the_dlls_functions(tmp_path):
    """Demo Quest with a DLL of its own (build.py dll), imported from its folder: the DLL is analysed at its own
    address (moved, as the program has that one), its exports name its functions, its files build with the Visual
    C++ that built it (2010 SP1; the program: 6.0), the rebuild check passes for each program, and an agent matches
    a DLL function written under the name the DLL exports it by."""
    from decompnet.app.engine import RomNetwork

    folder = DEMO / "build" / "dll"
    if not (folder / "demoquest.dll").exists():
        subprocess.run([sys.executable, str(DEMO / "build.py"), "dll"], check=True)
    said = []
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    slug = net.import_rom(folder, confirmed=True, log=said.append)
    ov = net.overview(slug)
    assert ov["ready"] and ov["rebuild"]["identical"], ov["rebuild"]
    assert re.search(r"by program: demoquest\.exe \d+, demoquest\.dll \d+$", ov["rebuild"]["detail"])
    assert any("the game's own DLLs beside demoquest.exe: demoquest.dll" in line for line in said)
    assert any("moved by its base relocations" in line for line in said)
    root = Path(net.project_row(slug)["root"])
    info = json.loads((root / "config" / "demoquest" / "functions.json").read_text())
    (dll,) = info["modules"]
    assert dll["preferred_base"] == 0x400000 and dll["image_base"] > 0x400000
    clamp = next(key for key, real in info["names"].items() if real == "clamp_s32")
    assert int(clamp[5:], 16) > dll["image_base"]
    dll_units = [unit for unit in info["units"] if int(unit[-8:], 16) >= dll["image_base"]]
    assert dll_units and all(unit.startswith("demoquest/") for unit in dll_units)
    assert scratch_pe.file_compilers(root, "demoquest") == {f"src/auto/{unit}.cpp": "10.0SP1" for unit in dll_units}
    agent, _ = net.local_agent("pc-agent")
    header = (GAME.parent / "game.h").read_text().split("#define NULL ((void *)0)", 1)[1].rsplit("#endif", 1)[0]
    assert net.forks.edit_file(agent, slug, "include/common.h", "", header, "the game's types").startswith("✅")
    symbol = next(fn["name"] for fns in info["units"].values() for fn in fns if fn["name"].endswith(clamp))
    res = net.attempt(agent, symbol, csource.get(GAME.read_text(), "clamp_s32"), slug)
    assert res["matched"], res["text"]
