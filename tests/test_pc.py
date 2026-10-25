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
EXE, MAP = DEMO / "build" / "demoquest.exe", DEMO / "build" / "demoquest.map"
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
    config.write_text(json.dumps({"units": {"sample": {}}}))
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

    if not EXE.exists():
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
    syms = _map(build / "demoquest.map")
    agent, _ = net.local_agent("pc-agent")
    sub = _by_address(build / "demoquest.map")
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
    syms, sub = _map(build / "demoquest.map"), _by_address(build / "demoquest.map")
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
        "identical": True, "detail": "identical: the code of its 2 functions (37 bytes)",
        "what": "The game's code, rebuilt from its delinked functions,"}


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
    feed line: the repairs, the stubs set aside, the pieces joined, the rebuild identical after each. The joins never
    take a stub, though the first one reads the frame of the function before it, as a piece would; a project at 3
    gets only the joins."""
    net, root, stubs = _project_made_before(tmp_path, monkeypatch, level)
    pid = net.project_row("p")["id"]
    consoles.get("pc").on_start(net, "p", log=lambda *_: None)
    said = [r["detail"] for r in net.store.all("SELECT detail FROM events WHERE project_id=? AND kind='analysis' "
                                               "ORDER BY id", pid)]
    expected = ["1 functions had a jump table", f"{len(EH_STUBS)} functions are exception-handling code",
                "1 pieces of functions Ghidra cut off"][0 if level == 1 else 2:]
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
                    "tools/configure.py, tools/names.py, config/game/file_compilers.json)"]
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
