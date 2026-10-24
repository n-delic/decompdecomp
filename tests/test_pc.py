"""PC (Windows) from scratch: a project generated for the user's .exe from Ghidra's analysis, worked on like a fork.

Demo Quest PC (projects/demo-pc) is built like a PC game of its time: Visual C++ 6.0 /O2, linked at 0x400000 without
relocations, with __stdcall and __fastcall functions, a jump table and a hand-written assembly helper. Importing it
must find every function with its exact extent, and agents' C must be judged by the project's own build and objdiff.
Visual C++ is proprietary, so the end-to-end part runs only where `msvc` was installed with its license accepted.
"""

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
from decompnet.forks.project import ForkProject

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
    subprocess.run([sys.executable, str(root / "tools/configure.py"), "TEST", "--msvc", str(compiler_root)],
                   check=True, capture_output=True, timeout=30)
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


def _map() -> dict[str, int]:
    """Symbol -> address of every function and variable, from the linker's map of the demo."""
    return {m.group(1): int(m.group(2), 16)
            for m in re.finditer(r"^\s+\S+\s+(\S+)\s+([0-9a-f]{8}) ", MAP.read_text(), re.M)}


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
    rename = {re.sub(r"^[_@]|@\d+$", "", s): (f"func_{a:08X}" if a < 0x402000 else f"D_{a:08X}")
              for s, a in syms.items() if not s.startswith("??") and s != "__fltused"}
    pattern = re.compile(r"\b(" + "|".join(sorted(rename, key=len, reverse=True)) + r")\b")
    sub = lambda text: pattern.sub(lambda m: rename[m.group(1)], text)
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
    assert vs2010.pack_version == "7.1" and "2005 to 2015 come next" in vs2010.cannot_match()[0]
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
    assert built((8, 0)).build is None and built((8, 0)).pack_version == "7.1"
    assert (built((7, 10)).version, built((7, 0)).version, built((10, 10)).version) == ("7.1", "7.0", "10.0")
    assert built((7, 10)).cannot_match() == [] and built((14, 29)).out_of_scope
    unknown = built((2, 50))  # not a Microsoft linker's version
    assert unknown.version is None and unknown.pack_version == "6.0" and unknown.blockers() == []


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
